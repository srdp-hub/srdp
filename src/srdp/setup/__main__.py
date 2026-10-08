"""CLI entry point: ``python -m srdp.setup`` runs the setup steps and exits.

First the database bootstrap, then the optional Garage step when
``[setup.garage]`` has ``enabled = true`` (see ``srdp.setup.garage``). The
whole config is validated first, so a bad Garage config stops setup before
it changes any database.
"""

import logging
import sys

from pydantic import ValidationError

from srdp.setup.bootstrap import SetupSettings, bootstrap_databases
from srdp.setup.garage import setup_garage

logger = logging.getLogger(__name__)


def main() -> None:
    """Validate the setup config, then run the database bootstrap and the Garage step if enabled."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        settings = SetupSettings()  # ty: ignore[missing-argument]
    except ValidationError as exc:
        # Pydantic's default message echoes the raw input, which holds the
        # role passwords, so report only the location and message of each error.
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"]) or "settings"
            logger.error("Invalid setup config (%s): %s", location, error["msg"])  # noqa: TRY400 -- a traceback would print the passwords
        sys.exit(1)

    bootstrap_databases(settings)
    logger.info("Database bootstrap complete.")
    if settings.garage.enabled:
        setup_garage(settings.garage)
    else:
        logger.info("Skipping the Garage step, [setup.garage] is not enabled.")
    logger.info("Setup complete.")


if __name__ == "__main__":
    main()
