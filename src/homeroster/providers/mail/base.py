"""The mail contract: send a multipart e-mail (text + HTML with inline images)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from ..base import Health


@dataclass(frozen=True)
class InlineImage:
    """An image embedded in the HTML part, referenced as ``<img src="cid:{cid}">``."""

    cid: str
    data: bytes
    subtype: str = "png"


class Mailer(ABC):
    provider_name: ClassVar[str] = ""
    label: ClassVar[str] = "E-mail"

    def __init__(self, options: dict[str, Any]):
        self.options = options

    @property
    @abstractmethod
    def admin_address(self) -> str:
        """Where alerts and test e-mails go."""

    @abstractmethod
    def configured(self) -> bool: ...

    @abstractmethod
    async def send(self, to: str, subject: str, html: str, text: str,
                   images: list[InlineImage] | tuple[InlineImage, ...] = ()) -> None: ...

    @abstractmethod
    async def health(self) -> Health: ...
