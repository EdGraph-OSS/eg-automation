"""ne-data-flow — runs all NE DataFlow steps in canonical order."""

import logging

from dotenv import load_dotenv

from .obfuscated_sync_to_esu import main as obfuscated_sync_to_esu
from .setup_esu import main as setup_esu
from .setup_tenant import main as setup_tenant
from .sync_from_act import main as sync_from_act
from .sync_from_sea import main as sync_from_sea

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    level=logging.DEBUG,
)
logger: logging.Logger = logging.getLogger(__name__)


def main() -> None:
    load_dotenv()
    setup_tenant()
    setup_esu()
    sync_from_sea()
    sync_from_act()
    obfuscated_sync_to_esu()


if __name__ == "__main__":
    main()
