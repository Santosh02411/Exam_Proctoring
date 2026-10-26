from app import create_app
from app.db_schema import SchemaOutOfDateError, verify_schema_current

app = create_app()

if __name__ == "__main__":
    # Local development server only (APP_ENV=development in your .env).
    try:
        verify_schema_current(app)
    except SchemaOutOfDateError as exc:
        raise SystemExit(f"\n{exc}\n")
    app.run(debug=True, host="0.0.0.0", port=5000)
