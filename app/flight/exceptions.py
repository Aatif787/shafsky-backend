from fastapi import HTTPException

class FlightDomainException(Exception):
    """Base domain exception for flight intelligence context."""
    def __init__(self, message: str, status_code: int = 500, code: str = "FLIGHT_ERROR"):
        self.message = message
        self.status_code = status_code
        self.code = code
        super().__init__(self.message)

class FlightNotFoundException(FlightDomainException):
    """Raised when the specified flight number cannot be located."""
    def __init__(self, flight_num: str, date: str):
        super().__init__(
            message=f"Flight '{flight_num}' on date '{date}' was not found.",
            status_code=404,
            code="FLIGHT_NOT_FOUND"
        )

class FlightProviderUnavailableException(FlightDomainException):
    """Raised when the active flight intelligence provider is unreachable."""
    def __init__(self, detail: str = "Flight provider is currently offline."):
        super().__init__(
            message=detail,
            status_code=503,
            code="FLIGHT_PROVIDER_UNAVAILABLE"
        )

class FlightProviderNotConfiguredException(FlightDomainException):
    """Raised when an operation is performed without an active flight provider implementation."""
    def __init__(self):
        super().__init__(
            message="No flight provider has been configured or registered.",
            status_code=500,
            code="PROVIDER_NOT_CONFIGURED"
        )
