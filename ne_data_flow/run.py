"""ne-data-flow — runs all NE DataFlow steps in canonical order."""

import asyncio
import logging

from dotenv import load_dotenv

from .obfuscated_sync_to_esu import _main as obfuscated_sync_to_esu
from .setup_esu import _main as setup_esu
from .setup_tenant import _main as setup_tenant
from .sync_from_act import _main as sync_from_act
from .sync_from_sea import _main as sync_from_sea

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger: logging.Logger = logging.getLogger(__name__)


async def _main() -> None:
    load_dotenv()
    await setup_tenant()
    await setup_esu()
    await sync_from_sea()
    await sync_from_act()
    await obfuscated_sync_to_esu()


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
