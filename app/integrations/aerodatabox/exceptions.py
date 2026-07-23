class AeroDataBoxException(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)

class InvalidFlightException(AeroDataBoxException):
    def __init__(self, message: str = "Flight not found or invalid."):
        super().__init__(code="INVALID_FLIGHT", message=message, status_code=400)

class ProviderUnavailableException(AeroDataBoxException):
    def __init__(self, message: str = "Flight provider service temporarily unavailable."):
        super().__init__(code="FLIGHT_PROVIDER_UNAVAILABLE", message=message, status_code=503)

class RateLimitException(AeroDataBoxException):
    def __init__(self, message: str = "Flight provider rate limit exceeded."):
        super().__init__(code="RATE_LIMIT_EXCEEDED", message=message, status_code=429)

class TimeoutException(AeroDataBoxException):
    def __init__(self, message: str = "Flight provider request timed out."):
        super().__init__(code="PROVIDER_TIMEOUT", message=message, status_code=504)
