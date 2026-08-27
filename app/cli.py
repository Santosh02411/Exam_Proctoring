import click

from app import db
from app.models import User


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Create all tables."""
        db.create_all()
        click.echo("Database tables created.")

    @app.cli.command("seed-admin")
    @click.option("--name", default="Admin User")
    @click.option("--email", default="admin@example.com")
    @click.option("--password", default="Admin123!")
    @click.option("--phone", default="9999999999")
    def seed_admin(name, email, password, phone):
        """Create a default admin account if it doesn't exist yet."""
        if User.query.filter_by(email=email).first():
            click.echo(f"User {email} already exists.")
            return
        from app.models import gen_user_id

        user = User(
            user_id=gen_user_id("admin"),
            name=name,
            email=email,
            phone=phone,
            role="admin",
            status="active",
            email_verified=True,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Admin created: {email} / {password}")
