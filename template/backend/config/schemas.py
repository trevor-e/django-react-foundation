from drf_foundation.schemas import Schema


class HealthCheck(Schema):
    status: str
