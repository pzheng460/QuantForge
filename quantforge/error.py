class QuantForgeError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(self.message)


class EngineBuildError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)


class SubscriptionError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)


class KlineSupportedError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)


class StrategyBuildError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)


class OrderError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)


class PositionModeError(QuantForgeError):
    def __init__(self, message: str):
        super().__init__(message)
