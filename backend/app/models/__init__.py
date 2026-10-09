"""모델 패키지.

Alembic autogenerate 와 create_all 이 전체 테이블을 보려면 여기서 모두 import 해야 한다.
"""

from app.models.base import Base
from app.models.device import Device, DeviceTombstone
from app.models.event import BroadcastEvent, BroadcastRecipient, DeviceEvent
from app.models.file import DownloadToken, File
from app.models.org import User, UserVillage, Village, Zone
from app.models.ota import OtaPackage, OtaToken
from app.models.schedule import Schedule
from app.models.system import CurrentConfig, DailyCostSummary

__all__ = [
    "Base",
    "BroadcastEvent",
    "BroadcastRecipient",
    "CurrentConfig",
    "DailyCostSummary",
    "Device",
    "DeviceEvent",
    "DeviceTombstone",
    "DownloadToken",
    "File",
    "OtaPackage",
    "OtaToken",
    "Schedule",
    "User",
    "UserVillage",
    "Village",
    "Zone",
]
from app.models.auth_log import LoginEvent  # noqa: E402,F401
