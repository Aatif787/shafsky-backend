"""
Structured Domain Exceptions for Flight Intelligence & Provider Integration.

Provides standardized HTTP status codes and error identifier strings.
"""

class FlightDomainException(Exception):
    """Base domain exception for flight intelligence context."""
    def __init__(self, message: str, status_code: int = 500, code: str = "FLIGHT_ERROR"):
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(self.message)


class InvalidFlightNumberException(FlightDomainException):
    """Raised when the specified flight number string is malformed or invalid."""
    def __init__(self, flight_num: str, detail: str = "Invalid flight number format."):
        super().__init__(
            message=f"Flight number '{flight_num}' is invalid. {detail}",
            status_code=400,
            code="INVALID_FLIGHT_NUMBER"
        )


class InvalidFlightDateException(FlightDomainException):
    """Raised when the specified date string is malformed or invalid."""
    def __init__(self, date_str: str, detail: str = "Expected YYYY-MM-DD format."):
        super().__init__(
            message=f"Date '{date_str}' is invalid. {detail}",
            status_code=400,
            code="INVALID_DATE"
        )


class FlightNotFoundException(FlightDomainException):
    """Raised when the specified flight number cannot be located on the specified date."""
    def __init__(self, flight_num: str, date: str):
        super().__init__(
            message=f"Flight '{flight_num}' on date '{date}' was not found.",
            status_code=404,
            code="FLIGHT_NOT_FOUND"
        )


class FlightRateLimitExceededException(FlightDomainException):
    """Raised when the external provider rate limit has been exceeded."""
    def __init__(self, detail: str = "Provider API rate limit exceeded. Please try again shortly."):
        super().__init__(
            message=detail,
            status_code=429,
            code="RATE_LIMIT_EXCEEDED"
        )


class FlightProviderTimeoutException(FlightDomainException):
    """Raised when the external flight provider request times out."""
    def __init__(self, detail: str = "Flight provider request timed out."):
        super().__init__(
            message=detail,
            status_code=504,
            code="PROVIDER_TIMEOUT"
        )


class FlightProviderUnavailableException(FlightDomainException):
    """Raised when the active flight intelligence provider is unreachable or returns 5xx error."""
    def __init__(self, detail: str = "Flight provider is currently offline or unavailable."):
        super().__init__(
            message=detail,
            status_code=503,
            code="PROVIDER_UNAVAILABLE"
        )


class FlightProviderNotConfiguredException(FlightDomainException):
    """Raised when an operation is performed without an active flight provider implementation."""
    def __init__(self):
        super().__init__(
            message="No flight provider has been configured or registered.",
            status_code=500,
            code="PROVIDER_NOT_CONFIGURED"
        )
