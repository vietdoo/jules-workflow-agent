# Contributing to Jules Workflow Agent

First off, thank you for considering contributing to Jules Workflow Agent! It's people like you that make open source such a great community.

## Where do I go from here?

If you've noticed a bug or have a feature request, please file an issue! It's better to open an issue first to discuss the proposed changes before starting to work on a Pull Request.

## Setting up your environment

1.  **Fork and Clone:** Fork the repository on GitHub and clone it locally.
2.  **Dependencies:** Ensure you have Python 3.10+, `pip`, and `pnpm` installed.
3.  **Install:** Run `make install` to install Python and Node dependencies.
4.  **Local Harness:** Run `make dev` to start the full local harness (Telegram, FastAPI control plane, Next.js studio).

See `README.md` and `LOCAL_HARNESS.md` for more detailed development instructions.

## Making Changes

-   Create a new branch for your feature or bug fix.
-   Make sure your changes are well-tested. You can run all checks using `make check`.
-   Update `README.md` or other documentation if you are changing functionality.

## Submitting a Pull Request

1.  Push your changes to your fork.
2.  Open a Pull Request against the `main` branch of this repository.
3.  Ensure all CI checks pass.
4.  A maintainer will review your PR and provide feedback.

## Code of Conduct

Please note that this project is released with a [Contributor Code of Conduct](CODE_OF_CONDUCT.md). By participating in this project you agree to abide by its terms.
