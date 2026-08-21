"""All SQL used by the research app — the single source of truth for the
event-table construction and the event-study analysis.

Convention notes (see data/README.md "迁移说明"):
- report_date is normalized to the LOCAL (Eastern) calendar date, because
  Yahoo stamps timestamps as "HH:MM:SS-04/05:00" which we parse as UTC-naive;
  subtracting 5h recovers the announcement day. Forward returns start at the
  first tradable bar strictly after that day.
- eps_yoy / eps_accel use same-order-quarter comparisons (rn-4 / rn-8).
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# 1. Build the `events` table from `prices` + `earnings`
# --------------------------------------------------------------------------- #
SQL_BUILD_EVENTS = """
CREATE OR REPLACE TABLE events AS
WITH
era AS (
  SELECT symbol,
         CAST(report_date - INTERVAL '5 hours' AS DATE) AS report_date,
         eps_est, eps_actual, surprise_pct,
         row_number() OVER (PARTITION BY symbol, report_date ORDER BY report_date) AS dup,
         row_number() OVER (PARTITION BY symbol ORDER BY report_date) AS ern
  FROM earnings
  WHERE eps_actual IS NOT NULL AND report_date IS NOT NULL
  QUALIFY dup = 1
),
pler AS (
  SELECT symbol, date, open, high, low, close, volume,
         row_number() OVER (PARTITION BY symbol ORDER BY date) AS rn,
         max(high)  OVER w AS high52,
         min(low)   OVER w AS low52,
         close / NULLIF(lag(close, 126) OVER w, 0) - 1 AS mom6,
         close / NULLIF(lag(close, 252) OVER w, 0) - 1 AS mom12,
         close / NULLIF(lag(close, 1) OVER w, 0) - 1 AS ret
  FROM prices
  WINDOW w AS (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
),
pler2 AS (
  SELECT *,
         stddev_samp(ret) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
             * sqrt(252) AS vol60
  FROM pler
),
ctl AS (
  SELECT e.*, p.rn AS ctx_rn, p.close AS ctx_close, p.high52, p.low52, p.mom6, p.mom12, p.vol60,
         y1.eps_actual AS yoy_actual, y2.eps_actual AS yoy2_actual
  FROM era e
  ASOF LEFT JOIN pler2 p ON e.symbol = p.symbol AND p.date <= e.report_date
  LEFT JOIN era y1 ON y1.symbol = e.symbol AND y1.ern = e.ern - 4
  LEFT JOIN era y2 ON y2.symbol = e.symbol AND y2.ern = e.ern - 8
),
fwd AS (
  SELECT c.*,
         b.close AS base_close,
         f1.close AS c20, f3.close AS c63, f8.close AS c84,
         f6.close AS c126, f9.close AS c168, f9b.close AS c189,
         f12.close AS c252, f15.close AS c315, f18.close AS c378, f24.close AS c504
  FROM ctl c
  JOIN pler2 b  ON b.symbol  = c.symbol AND b.rn  = c.ctx_rn + 1
  LEFT JOIN pler2 f1  ON f1.symbol  = c.symbol AND f1.rn  = c.ctx_rn + 1 + 20
  LEFT JOIN pler2 f3  ON f3.symbol  = c.symbol AND f3.rn  = c.ctx_rn + 1 + 63
  LEFT JOIN pler2 f8  ON f8.symbol  = c.symbol AND f8.rn  = c.ctx_rn + 1 + 84
  LEFT JOIN pler2 f6  ON f6.symbol  = c.symbol AND f6.rn  = c.ctx_rn + 1 + 126
  LEFT JOIN pler2 f9  ON f9.symbol  = c.symbol AND f9.rn  = c.ctx_rn + 1 + 168
  LEFT JOIN pler2 f9b ON f9b.symbol = c.symbol AND f9b.rn = c.ctx_rn + 1 + 189
  LEFT JOIN pler2 f12 ON f12.symbol = c.symbol AND f12.rn = c.ctx_rn + 1 + 252
  LEFT JOIN pler2 f15 ON f15.symbol = c.symbol AND f15.rn = c.ctx_rn + 1 + 315
  LEFT JOIN pler2 f18 ON f18.symbol = c.symbol AND f18.rn = c.ctx_rn + 1 + 378
  LEFT JOIN pler2 f24 ON f24.symbol = c.symbol AND f24.rn = c.ctx_rn + 1 + 504
)
SELECT symbol, report_date, eps_est, eps_actual, surprise_pct,
       CASE WHEN yoy_actual IS NOT NULL AND abs(yoy_actual) > 1e-9
            THEN eps_actual / abs(yoy_actual) - 1 END AS eps_yoy_pct,
       CASE WHEN eps_yoy_pct IS NOT NULL AND yoy2_actual IS NOT NULL AND abs(yoy2_actual) > 1e-9
            THEN eps_yoy_pct - (yoy_actual / abs(yoy2_actual) - 1) END AS eps_accel_pct,
       ctx_close AS close,
       ctx_close / high52 - 1 AS px_high52_pct,
       CASE WHEN high52 > low52 THEN (ctx_close - low52) / (high52 - low52) END AS px_low52_pos,
       mom6 AS mom6_pct, mom12 AS mom12_pct, vol60,
       c20 / base_close - 1 AS fwd20,
       c63 / base_close - 1 AS fwd63,
       c84 / base_close - 1 AS fwd84,
       c126 / base_close - 1 AS fwd126,
       c168 / base_close - 1 AS fwd168,
       c189 / base_close - 1 AS fwd189,
       c252 / base_close - 1 AS fwd252,
       c315 / base_close - 1 AS fwd315,
       c378 / base_close - 1 AS fwd378,
       c504 / base_close - 1 AS fwd504
FROM fwd
WHERE report_date BETWEEN DATE '2023-01-01' AND DATE '2026-06-01'
  AND COALESCE(fwd20, fwd63, fwd126, fwd252) IS NOT NULL
"""

# --------------------------------------------------------------------------- #
# 2. Event-study analysis (shared base CTE: surprise filter + 1/99% clipping)
# --------------------------------------------------------------------------- #
_BASE = """
WITH base AS (
  SELECT symbol, report_date, surprise_pct, px_low52_pos, mom12_pct,
         fwd20, fwd63, fwd126, fwd252
  FROM events WHERE surprise_pct IS NOT NULL
),
bnd AS (
  SELECT quantile_cont(surprise_pct, 0.01) lo, quantile_cont(surprise_pct, 0.99) hi,
         quantile_cont(mom12_pct, 0.01) mlo, quantile_cont(mom12_pct, 0.99) mhi
  FROM base
),
clip AS (
  SELECT b.*, greatest(least(b.surprise_pct, n.hi), n.lo) AS s,
         greatest(least(b.mom12_pct, n.mhi), n.mlo) AS m
  FROM base b CROSS JOIN bnd n
)
"""

Q_DECILES = _BASE + """
SELECT decile, count(*) AS n, round(median(s), 2) AS med_surprise,
       round(avg(f20)*100, 3) AS f20_mean, round(avg(f63)*100, 3) AS f63_mean,
       round(avg(f126)*100, 3) AS f126_mean, round(avg(f252)*100, 3) AS f252_mean,
       round(median(f20)*100, 3) AS f20_med, round(median(f63)*100, 3) AS f63_med,
       round(median(f126)*100, 3) AS f126_med, round(median(f252)*100, 3) AS f252_med
FROM (
  SELECT c.*, ntile(10) OVER (ORDER BY c.s) AS decile,
         fwd20 AS f20, fwd63 AS f63, fwd126 AS f126, fwd252 AS f252
  FROM clip c
) GROUP BY decile ORDER BY decile
"""

Q_LEAD_TIME = _BASE + """
SELECT '1个月' AS 持有期, 20 AS 交易日,
       round(avg(fwd20)*100,3) AS avg_pct, round(median(fwd20)*100,3) AS med_pct,
       count(fwd20) AS n
FROM (SELECT c.*, ntile(10) OVER (ORDER BY c.s) AS decile FROM clip c)
WHERE decile IN (9,10)
UNION ALL SELECT '3个月', 63, round(avg(fwd63)*100,3), round(median(fwd63)*100,3), count(fwd63)
FROM (SELECT c.*, ntile(10) OVER (ORDER BY c.s) AS decile FROM clip c) WHERE decile IN (9,10)
UNION ALL SELECT '6个月', 126, round(avg(fwd126)*100,3), round(median(fwd126)*100,3), count(fwd126)
FROM (SELECT c.*, ntile(10) OVER (ORDER BY c.s) AS decile FROM clip c) WHERE decile IN (9,10)
UNION ALL SELECT '12个月', 252, round(avg(fwd252)*100,3), round(median(fwd252)*100,3), count(fwd252)
FROM (SELECT c.*, ntile(10) OVER (ORDER BY c.s) AS decile FROM clip c) WHERE decile IN (9,10)
"""

Q_INTERACTION_POS = _BASE + """
SELECT label_pos AS 位置, label_surp AS 超预期, count(*) AS n,
       round(median(fwd20)*100,3) AS fwd20_med_pct,
       round(median(fwd63)*100,3) AS fwd63_med_pct,
       round(median(fwd126)*100,3) AS fwd126_med_pct,
       round(median(fwd252)*100,3) AS fwd252_med_pct
FROM (
  SELECT c.*, ntile(4) OVER (ORDER BY c.px_low52_pos) AS pq,
         ntile(4) OVER (ORDER BY c.s) AS sq,
         CASE WHEN ntile(4) OVER (ORDER BY c.px_low52_pos) = 1 THEN '低位(52周区间底部25%)'
              WHEN ntile(4) OVER (ORDER BY c.px_low52_pos) IN (2,3) THEN '中位'
              ELSE '高位(顶部25%)' END AS label_pos,
         CASE WHEN ntile(4) OVER (ORDER BY c.s) = 4 THEN '超预期top25%'
              ELSE '超预期bottom25%' END AS label_surp
  FROM clip c
) WHERE sq IN (1,4)
GROUP BY label_pos, label_surp ORDER BY label_surp, label_pos
"""

Q_INTERACTION_MOM = _BASE + """
SELECT label_mom AS 动量, label_surp AS 超预期, count(*) AS n,
       round(median(fwd20)*100,3) AS fwd20_med_pct,
       round(median(fwd63)*100,3) AS fwd63_med_pct,
       round(median(fwd126)*100,3) AS fwd126_med_pct,
       round(median(fwd252)*100,3) AS fwd252_med_pct
FROM (
  SELECT c.*, ntile(4) OVER (ORDER BY c.m) AS mq,
         ntile(4) OVER (ORDER BY c.s) AS sq,
         CASE WHEN ntile(4) OVER (ORDER BY c.m) = 1 THEN '弱势(动量bottom25%)'
              WHEN ntile(4) OVER (ORDER BY c.m) = 4 THEN '强势(动量top25%)' END AS label_mom,
         CASE WHEN ntile(4) OVER (ORDER BY c.s) = 4 THEN '超预期top25%'
              ELSE '超预期bottom25%' END AS label_surp
  FROM clip c
) WHERE sq IN (1,4) AND mq IN (1,4)
GROUP BY label_mom, label_surp ORDER BY label_surp, label_mom
"""


def q_spotlight(symbols: list[str]) -> str:
    ins = ",".join(f"'{s}'" for s in symbols)
    return f"""
    WITH ranked AS (
      SELECT symbol, report_date, surprise_pct, mom12_pct, fwd126, fwd252,
             row_number() OVER (PARTITION BY symbol ORDER BY fwd126 DESC) AS rn
      FROM events
      WHERE symbol IN ({ins}) AND surprise_pct IS NOT NULL AND fwd126 IS NOT NULL
    )
    SELECT symbol, report_date, surprise_pct, mom12_pct, fwd126, fwd252
    FROM ranked WHERE rn = 1 ORDER BY symbol
    """


# --------------------------------------------------------------------------- #
# 4. Theme-resonance validation (T3): do concentrated peer surprises lift
#    forward returns on top of the event's own surprise?
#    Expects a registered table `themes(symbol VARCHAR, theme VARCHAR)`.
# --------------------------------------------------------------------------- #
SQL_THEME_RESONANCE = """
WITH ev AS (
  SELECT e.symbol, e.report_date, e.surprise_pct, e.fwd126, e.fwd252, t.theme
  FROM events e JOIN themes t ON t.symbol = e.symbol
  WHERE e.surprise_pct IS NOT NULL
),
thr AS (
  SELECT quantile_cont(surprise_pct, 0.9) AS hi, quantile_cont(surprise_pct, 0.25) AS lo
  FROM ev
),
tpr AS (
  SELECT ev.*,
    (SELECT count(DISTINCT p.symbol)
     FROM ev p
     WHERE p.theme = ev.theme
       AND p.symbol <> ev.symbol
       AND p.report_date > ev.report_date - INTERVAL 120 DAY
       AND p.report_date <= ev.report_date
       AND p.surprise_pct >= (SELECT hi FROM thr)) AS peer_cnt
  FROM ev
)
SELECT s_bucket, resonance, n,
       round(f126_med, 3) AS fwd126_med_pct, round(f252_med, 3) AS fwd252_med_pct,
       round(f252_avg, 3) AS fwd252_avg_pct
FROM (
  SELECT CASE WHEN tpr.surprise_pct >= (SELECT hi FROM thr) THEN '超预期top10%'
              WHEN tpr.surprise_pct <= (SELECT lo FROM thr) THEN '超预期bottom25%'
              ELSE '中位' END AS s_bucket,
         CASE WHEN tpr.peer_cnt = 0 THEN '0同行'
              WHEN tpr.peer_cnt = 1 THEN '1同行' ELSE '2+同行' END AS resonance,
         count(*) AS n,
         median(fwd126) * 100 AS f126_med,
         median(fwd252) * 100 AS f252_med,
         avg(fwd252) * 100 AS f252_avg
  FROM tpr
  GROUP BY 1, 2
) ORDER BY s_bucket, resonance
"""

#: theme sizes (for transparency: how many symbols each theme spans)
SQL_THEME_SIZES = """
SELECT t.theme, count(DISTINCT t.symbol) AS n_sym
FROM themes t GROUP BY t.theme ORDER BY n_sym DESC
"""

# --------------------------------------------------------------------------- #
# 5. Repeat-winner validation (T5): prior doubling / consecutive top surprises
# --------------------------------------------------------------------------- #
SQL_REPEAT = """
WITH ev AS (
  SELECT symbol, report_date, surprise_pct, fwd126, fwd252 FROM events
  WHERE surprise_pct IS NOT NULL
),
thr AS (SELECT quantile_cont(surprise_pct, 0.9) AS hi, quantile_cont(surprise_pct, 0.25) AS lo FROM ev),
hist AS (
  SELECT e.*,
    (SELECT count(*) FROM ev p
     WHERE p.symbol = e.symbol AND p.report_date < e.report_date
       AND p.report_date > e.report_date - INTERVAL 365 DAY
       AND p.fwd252 IS NOT NULL AND p.fwd252 >= 1.0) AS prior_doubles,
    (SELECT count(*) FROM ev p
     WHERE p.symbol = e.symbol AND p.report_date < e.report_date
       AND p.report_date > e.report_date - INTERVAL 365 DAY
       AND p.surprise_pct >= (SELECT hi FROM thr)) AS prior_top_surp
  FROM ev e
)
SELECT s_bucket, prior_doubles_label, prior_top_surp_label, n,
       round(f126, 3) AS fwd126_med_pct, round(f252, 3) AS fwd252_med_pct, round(f252a, 3) AS fwd252_avg_pct
FROM (
  SELECT CASE WHEN h.surprise_pct >= (SELECT hi FROM thr) THEN '超预期top10%'
              WHEN h.surprise_pct <= (SELECT lo FROM thr) THEN '超预期bottom25%'
              ELSE '中位' END AS s_bucket,
         CASE WHEN h.prior_doubles = 0 THEN '0次'
              WHEN h.prior_doubles = 1 THEN '1次' ELSE '2+次' END AS prior_doubles_label,
         CASE WHEN h.prior_top_surp = 0 THEN '0次'
              WHEN h.prior_top_surp = 1 THEN '1次' ELSE '2+次' END AS prior_top_surp_label,
         count(*) AS n, median(fwd126)*100 AS f126, median(fwd252)*100 AS f252, avg(fwd252)*100 AS f252a
  FROM hist h
  GROUP BY 1, 2, 3
) ORDER BY s_bucket, prior_doubles_label
"""


# --------------------------------------------------------------------------- #
# 6. Composite signal (T7): theme resonance x momentum x surprise
#    Expects a registered table `themes(symbol VARCHAR, theme VARCHAR)`.
#    Momentum/surprise buckets follow K3 (top/bottom quartiles); resonance
#    follows K8 (same-theme peers with top-10% surprise in trailing 120d).
# --------------------------------------------------------------------------- #
SQL_COMPOSITE = """
WITH ev AS (
  SELECT e.symbol, e.report_date, e.surprise_pct, e.mom12_pct, e.fwd126, e.fwd252, t.theme
  FROM events e JOIN themes t ON t.symbol = e.symbol
  WHERE e.surprise_pct IS NOT NULL AND e.mom12_pct IS NOT NULL
),
thr AS (
  SELECT quantile_cont(surprise_pct, 0.90) AS shi10,
         quantile_cont(surprise_pct, 0.25) AS slo25,
         quantile_cont(surprise_pct, 0.75) AS shi25,
         quantile_cont(mom12_pct, 0.25) AS mlo25,
         quantile_cont(mom12_pct, 0.75) AS mhi25
  FROM ev
),
tpr AS (
  SELECT ev.*,
    (SELECT count(DISTINCT p.symbol)
     FROM ev p
     WHERE p.theme = ev.theme AND p.symbol <> ev.symbol
       AND p.report_date > ev.report_date - INTERVAL 120 DAY
       AND p.report_date <= ev.report_date
       AND p.surprise_pct >= (SELECT shi10 FROM thr)) AS peer_cnt
  FROM ev
)
SELECT m_b, s_b, r_b, count(*) AS n,
       round(median(fwd126)*100, 3) AS fwd126_med_pct,
       round(median(fwd252)*100, 3) AS fwd252_med_pct,
       round(avg(fwd252)*100, 3) AS fwd252_avg_pct
FROM (
  SELECT tpr.*,
    CASE WHEN tpr.surprise_pct >= (SELECT shi25 FROM thr) THEN '超预期top25%'
         WHEN tpr.surprise_pct <= (SELECT slo25 FROM thr) THEN '超预期bottom25%'
         ELSE '中位' END AS s_b,
    CASE WHEN tpr.mom12_pct >= (SELECT mhi25 FROM thr) THEN '强势(动量top25%)'
         WHEN tpr.mom12_pct <= (SELECT mlo25 FROM thr) THEN '弱势(动量bottom25%)'
         ELSE '中位动量' END AS m_b,
    CASE WHEN tpr.peer_cnt = 0 THEN '0同行'
         WHEN tpr.peer_cnt = 1 THEN '1同行' ELSE '2+同行' END AS r_b
  FROM tpr
) GROUP BY m_b, s_b, r_b ORDER BY m_b, s_b, r_b
"""


# --------------------------------------------------------------------------- #
# 3. Surge screening: apply the discovered rules to the CURRENT market
# --------------------------------------------------------------------------- #
#: Prep: build a temp table `screen_base` = latest report (>= 2026-01-01) per
#: symbol joined with its latest price context, YoY / acceleration, and the
#: real-time theme resonance (K8/K11) — count of same-theme peers whose report
#: in the trailing 120 days had top-10% surprise (validated firing level).
#: Requires a registered table `themes(symbol VARCHAR, theme VARCHAR)`.
SQL_SCREEN_PREP = """
CREATE TEMP TABLE screen_base AS
WITH
lastrep AS (
  SELECT symbol, report_date, eps_est, eps_actual, surprise_pct,
         row_number() OVER (PARTITION BY symbol ORDER BY report_date DESC) AS rn
  FROM earnings
  WHERE eps_actual IS NOT NULL AND surprise_pct IS NOT NULL
  QUALIFY rn = 1
),
prev AS (
  SELECT symbol, report_date, eps_actual,
         row_number() OVER (PARTITION BY symbol ORDER BY report_date DESC) AS rn
  FROM earnings WHERE eps_actual IS NOT NULL
),
px AS (
  SELECT * FROM (
    SELECT symbol, date AS px_date, close,
           count(*) OVER (PARTITION BY symbol) AS nrows,
           max(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS high52,
           min(low)  OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS low52,
           close / NULLIF(lag(close, 126) OVER w, 0) - 1 AS mom6,
           close / NULLIF(lag(close, 252) OVER w, 0) - 1 AS mom12,
           (close - min(low)  OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW))
               / NULLIF(max(high) OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
                        - min(low)  OVER (PARTITION BY symbol ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW), 0) AS pos52,
           row_number() OVER (PARTITION BY symbol ORDER BY date DESC) AS rn
    FROM prices
    WINDOW w AS (PARTITION BY symbol ORDER BY date)
  ) WHERE rn = 1
),
ths AS (  -- validated research thresholds from the events table
  SELECT quantile_cont(surprise_pct, 0.90) AS shi10,
         quantile_cont(surprise_pct, 0.75) AS shi25,
         quantile_cont(mom12_pct, 0.75) AS mhi25
  FROM events WHERE surprise_pct IS NOT NULL AND mom12_pct IS NOT NULL
),
anchor AS (SELECT max(date) AS d FROM prices)
SELECT l.symbol, l.report_date AS last_report, l.eps_est, l.eps_actual, l.surprise_pct,
       p.px_date, p.close, p.mom6, p.mom12, p.pos52,
       p.close / p.high52 - 1 AS dist_high,
       l.eps_actual / NULLIF(abs(y1.eps_actual), 0) - 1 AS eps_yoy,
       y1.eps_actual / NULLIF(abs(y2.eps_actual), 0) - 1 AS yoy_prev,
       (SELECT count(DISTINCT p.symbol)
        FROM earnings p
        JOIN themes tp ON tp.symbol = p.symbol
        WHERE p.eps_actual IS NOT NULL AND p.surprise_pct IS NOT NULL
          AND tp.theme = t.theme AND p.symbol <> l.symbol
          AND CAST(p.report_date - INTERVAL '5 hours' AS DATE) > (SELECT d FROM anchor) - INTERVAL 120 DAY
          AND CAST(p.report_date - INTERVAL '5 hours' AS DATE) <= (SELECT d FROM anchor)
          AND p.surprise_pct >= (SELECT shi10 FROM ths)) AS resonance,
       CASE WHEN l.surprise_pct >= (SELECT shi25 FROM ths) THEN 1 ELSE 0 END AS s_top25,
       CASE WHEN p.mom12 >= (SELECT mhi25 FROM ths) THEN 1 ELSE 0 END AS m_top25
FROM lastrep l
JOIN px p USING (symbol)
JOIN themes t ON t.symbol = l.symbol
LEFT JOIN prev y1 ON y1.symbol = l.symbol AND y1.rn = 4
LEFT JOIN prev y2 ON y2.symbol = l.symbol AND y2.rn = 8
WHERE l.report_date >= DATE '2026-01-01'
  AND p.high52 > p.low52
  AND p.pos52 BETWEEN 0 AND 1
  AND p.nrows >= 300   -- need real 12m history (excludes fresh listings/spin-offs)
"""

#: Rank: clip at 1/99%, z-score each rule, weighted composite.
#: Weights come from the event study: surprise 1.0, 12m momentum 1.0
#: (the "strong momentum x surprise" combo was the best cell), eps YoY 0.5,
#: 6m momentum 0.5, 52-week strength 0.5.
SQL_SCREEN_RANK = """
WITH b AS (
  SELECT screen_base.*,
         greatest(least(surprise_pct, q.shi), q.slo) AS s,
         greatest(least(eps_yoy,      q.yhi), q.ylo) AS y,
         greatest(least(mom12,        q.mhi), q.mlo) AS m12,
         greatest(least(pos52,        q.phi), q.plo) AS p
  FROM screen_base
  CROSS JOIN (SELECT quantile_cont(surprise_pct, 0.01) slo, quantile_cont(surprise_pct, 0.99) shi,
                     quantile_cont(eps_yoy,      0.01) ylo, quantile_cont(eps_yoy,      0.99) yhi,
                     quantile_cont(mom12,        0.01) mlo, quantile_cont(mom12,        0.99) mhi,
                     quantile_cont(pos52,        0.01) plo, quantile_cont(pos52,        0.99) phi
              FROM screen_base) q
),
zs AS (
  SELECT *,
         (s   - avg(s)   OVER ()) / NULLIF(stddev_samp(s)   OVER (), 0) AS z_s,
         (y   - avg(y)   OVER ()) / NULLIF(stddev_samp(y)   OVER (), 0) AS z_y,
         (m12 - avg(m12) OVER ()) / NULLIF(stddev_samp(m12) OVER (), 0) AS z_m,
         (p   - avg(p)   OVER ()) / NULLIF(stddev_samp(p)   OVER (), 0) AS z_p,
         (mom6 - avg(mom6) OVER ()) / NULLIF(stddev_samp(mom6) OVER (), 0) AS z_m6
  FROM b
)
SELECT symbol, last_report AS report_date, px_date, close,
       surprise_pct, eps_yoy, eps_yoy - yoy_prev AS eps_accel,
       mom6, mom12, pos52, dist_high, resonance, s_top25, m_top25,
       CASE WHEN mom12 >= 2.5 AND mom6 >= 1.0 THEN 1 ELSE 0 END AS top_risk,
       round(1.0*z_s + 0.5*z_y + 1.0*z_m + 0.5*z_m6 + 0.5*z_p, 3) AS score
FROM zs ORDER BY score DESC
"""

# --------------------------------------------------------------------------- #
# 3c. Exit discipline (K2 evidence): forward-drift decay by holding horizon.
#     Median/mean fwd return at 1..24 months for the system-entry population
#     (top surprise x strong momentum) vs weaker surprise. Answers "when to
#     get out": still rising through 12m? flatten or fade after?
# --------------------------------------------------------------------------- #
SQL_EXIT_DECAY = """
WITH ths AS (
  SELECT quantile_cont(surprise_pct, 0.75) shi25, quantile_cont(mom12_pct, 0.75) mhi25
  FROM events WHERE surprise_pct IS NOT NULL AND mom12_pct IS NOT NULL
),
tagged AS (
  SELECT *, CASE WHEN surprise_pct >= (SELECT shi25 FROM ths) AND mom12_pct >= (SELECT mhi25 FROM ths)
                 THEN '1 系统人群(超预期top×强动量)'
                 WHEN surprise_pct >= (SELECT shi25 FROM ths) THEN '2 超预期top25(非强动量)'
                 ELSE '3 其余' END AS label
  FROM events
)
(SELECT label, '1个月' AS h, round(median(fwd20)*100,1) AS med_pct, round(avg(fwd20)*100,1) AS avg_pct, count(fwd20) AS n FROM tagged GROUP BY label)
UNION ALL (SELECT label, '4个月', round(median(fwd84)*100,1), round(avg(fwd84)*100,1), count(fwd84)   FROM tagged GROUP BY label)
UNION ALL (SELECT label, '8个月', round(median(fwd168)*100,1), round(avg(fwd168)*100,1), count(fwd168) FROM tagged GROUP BY label)
UNION ALL (SELECT label, '12个月', round(median(fwd252)*100,1), round(avg(fwd252)*100,1), count(fwd252) FROM tagged GROUP BY label)
UNION ALL (SELECT label, '15个月', round(median(fwd315)*100,1), round(avg(fwd315)*100,1), count(fwd315) FROM tagged GROUP BY label)
UNION ALL (SELECT label, '18个月', round(median(fwd378)*100,1), round(avg(fwd378)*100,1), count(fwd378) FROM tagged GROUP BY label)
UNION ALL (SELECT label, '24个月', round(median(fwd504)*100,1), round(avg(fwd504)*100,1), count(fwd504) FROM tagged GROUP BY label)
"""
