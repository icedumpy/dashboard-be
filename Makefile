clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

migrate:
	alembic -c /app/alembic.ini upgrade head

revert:
	alembic -c ./api/alembic.ini downgrade -1


dev:
	docker compose up