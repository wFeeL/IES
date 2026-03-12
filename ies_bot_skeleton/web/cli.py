from __future__ import annotations

from pathlib import Path

import click
from flask.cli import with_appcontext

from ..application.legacy_import import run_legacy_import
from .extensions import db
from .models import GameSession, User
from .services.seed import ensure_seed_data


@click.command("seed")
@click.option("--admin-password", default="admin123", show_default=True)
@click.option("--analyst-password", default="analyst123", show_default=True)
@with_appcontext
def seed_command(admin_password: str, analyst_password: str) -> None:
    result = ensure_seed_data(
        admin_password=admin_password,
        analyst_password=analyst_password,
    )
    click.echo(f"Seed completed: {result}")


@click.command("create-admin")
@click.argument("username")
@click.argument("password")
@with_appcontext
def create_admin_command(username: str, password: str) -> None:
    user = db.session.query(User).filter_by(username=username).one_or_none()
    if user is None:
        user = User(username=username, role="admin", is_active=True)
    user.role = "admin"
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Admin '{username}' created/updated")


@click.command("import-legacy")
@click.option("--session", "session_id", type=int, required=True)
@click.option("--state", "state_path", type=click.Path(path_type=Path), default=None)
@click.option("--lots-dir", "lots_dir", type=click.Path(path_type=Path), default=None)
@with_appcontext
def import_legacy_command(session_id: int, state_path: Path | None, lots_dir: Path | None) -> None:
    row = db.session.get(GameSession, session_id)
    if row is None:
        raise click.ClickException(f"Session {session_id} not found")

    kwargs = {"session_id": session_id}
    if state_path is not None:
        kwargs["state_path"] = state_path
    if lots_dir is not None:
        kwargs["lots_dir"] = lots_dir

    report = run_legacy_import(**kwargs)
    click.echo(f"Legacy import report: {report}")


def init_cli(app) -> None:
    app.cli.add_command(seed_command)
    app.cli.add_command(create_admin_command)
    app.cli.add_command(import_legacy_command)
