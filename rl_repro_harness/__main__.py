"""Allow ``python -m rl_repro_harness`` to invoke the CLI."""

from .cli import main

raise SystemExit(main())
