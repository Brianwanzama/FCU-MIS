import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateStorage(FileSystemStorage):
    """
    Storage for uploads that must never be served as static files. The location
    is read from settings on every use, so it can be pointed at a persistent
    volume in production (and at a temporary folder in tests).
    """

    @property
    def base_location(self):
        return settings.PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    @property
    def base_url(self):
        return None


def get_private_storage():
    return PrivateStorage()
