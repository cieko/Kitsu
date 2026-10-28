"""Schedules public interface."""

from .autocomplete import schedule_name_autocomplete as schedule_name_autocomplete
from .cog import Schedules as Schedules
from .cog import setup as setup
from .management import ScheduleManagement as ScheduleManagement
from .views import DeleteAllSchedulesView as DeleteAllSchedulesView
from .views import EditScheduleModal as EditScheduleModal
from .views import ScheduleListView as ScheduleListView

__all__ = [
    "DeleteAllSchedulesView",
    "EditScheduleModal",
    "ScheduleListView",
    "ScheduleManagement",
    "Schedules",
    "schedule_name_autocomplete",
    "setup",
]
