# 主题共振验证：同主题同行超预期是否叠加前向收益

- 数据：events 表 6,895 条（2023-01~2026-06，499 只）；主题 = GICS 细分行业（SP500 元数据，无手工挑票）
- 定义：事件的**同行数** = 最近120天内、同细分行业、其他股票也出现超预期 top10% 财报的标的数
- 检验：同一超预期档内，同行数越高，前向收益是否越高（若成立=主题共振）

## 分桶结果（自身超预期档 × 同行共振数）

| s_bucket     | resonance   |    n |   fwd126_med_pct |   fwd252_med_pct |   fwd252_avg_pct |
|:-------------|:------------|-----:|-----------------:|-----------------:|-----------------:|
| 中位           | 0同行         | 2777 |            4.352 |           10.29  |             21.6 |
| 中位           | 1同行         | 1143 |            4.841 |           12.515 |             21.6 |
| 中位           | 2+同行        |  555 |           10.176 |           16.992 |             22.4 |
| 超预期bottom25% | 0同行         | 1156 |            4.169 |            9.516 |             19.3 |
| 超预期bottom25% | 1同行         |  394 |            6.557 |           11.802 |             17.4 |
| 超预期bottom25% | 2+同行        |  174 |            6.36  |           12.172 |             18.1 |
| 超预期top10%    | 0同行         |  375 |            5.072 |           14.366 |             49.2 |
| 超预期top10%    | 1同行         |  198 |           12.236 |           22.391 |             40.4 |
| 超预期top10%    | 2+同行        |  116 |            8.161 |           20.611 |             26.3 |

> 读法：横向同行从 0→1→2+，看 fwd252 中位是否单调上升。

## 主题规模（按股票数 Top 20）

| theme                                        |   n_sym |
|:---------------------------------------------|--------:|
| Health Care Equipment                        |      16 |
| Electric Utilities                           |      15 |
| Semiconductors                               |      15 |
| Application Software                         |      14 |
| Industrial Machinery & Supplies & Components |      14 |
| Asset Management & Custody Banks             |      12 |
| Aerospace & Defense                          |      12 |
| Multi-Utilities                              |      12 |
| Building Products                            |       9 |
| Oil & Gas Exploration & Production           |       9 |
| Financial Exchanges & Data                   |       9 |
| Transaction & Payment Processing Services    |       9 |
| Technology Hardware, Storage & Peripherals   |       9 |
| Packaged Foods & Meats                       |       8 |
| Property & Casualty Insurance                |       8 |
| Hotels, Resorts & Cruise Lines               |       8 |
| Life Sciences Tools & Services               |       8 |
| Biotechnology                                |       8 |
| Diversified Banks                            |       7 |
| Pharmaceuticals                              |       7 |

## 结论

**主题共振假设成立，且形态比预期更有意思：**

1. **共振对「平庸超预期」最有用**：中位档 12m 中位 10.3% → 17.0%（+6.7pp，单调），2+同行时已反超独立 top10% 事件（17.0% vs 14.4%）——「主题火了，跟着喝汤」是真实效应。
2. **共振也加成 top10%**：1同行 22.4%（+8.0pp）最强，2+同行 20.6%（+6.2pp）；非单调，可能是 2+同行 常出现在周期末段拥挤时点。
3. **共振 = 更可交易的信号**：top10% 档中位-均值差，0同行 +34.8pp vs 2+同行 +5.7pp——独立 top 事件靠少数冷门爆款（如逼空型）撑起均值，共振事件的收益分布更均匀，中位更可信。
判定：**成立（同一超预期档内，同行共振整体抬升前向收益中位）**

## 局限

1. 主题=GICS细分行业是官方分类，不捕捉跨行业产业链（如存储集群跨 Semiconductors/硬件/存储）——真链式共振可能被低估。
2. 同行数未按主题规模归一（大主题天然同行多）；作为对照主题规模列在上面。
3. 未计交易成本/滑点；Universe 幸存者偏差。
4. 只检验了「同行也超预期」一个共振维度；动量/行业的共振未测。
