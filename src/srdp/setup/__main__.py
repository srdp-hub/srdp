"""CLI entry point: ``python -m srdp.setup`` runs the setup steps and exits.

First the database bootstrap, then the optional Garage step when
``GARAGE_ADMIN_TOKEN`` is set (see ``srdp.setup.garage``).
"""

import logging
import sys

from pydantic import ValidationError

from srdp.setup.bootstrap import bootstrap_databases
from srdp.setup.garage import garage_requested, setup_garage

logger = logging.getLogger(__name__)


def main() -> None:
    """Run the database bootstrap, then the Garage step if it is configured."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        bootstrap_databases()
        logger.info("Database bootstrap complete.")
        if garage_requested():
            setup_garage()
        else:
            logger.info("Skipping the Garage step, GARAGE_ADMIN_TOKEN is not set.")
    except ValidationError as exc:
        # Pydantic's default message echoes the raw input, which holds the
        # role passwords, so report only the location and message of each error.
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"]) or "settings"
            logger.error("Invalid setup config (%s): %s", location, error["msg"])  # noqa: TRY400 -- a traceback would print the passwords
        sys.exit(1)
    logger.info("Setup complete.")


if __name__ == "__main__":
    main()
