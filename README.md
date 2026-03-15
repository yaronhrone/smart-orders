# Data Summarization Service

## Overview

This project is a backend system that:

- Fetches news articles from the New York Times public API
- Stores them in a PostgreSQL database
- Provides RESTful endpoints to query stored data
- Generates AI-powered summaries using OpenAI
- Uses Redis for caching
- Automatically fetches new data every 6 hours
- Runs inside Docker containers

The system is built using **Django** and **Django REST Framework**, following clean architecture and separation of concerns.

---

## Architecture

The system follows a layered architecture:

Client → API Layer → Service Layer → Database / AI / Cache

### Components

- Django REST API
- PostgreSQL (data storage)
- Redis (caching layer)
- APScheduler (automatic fetching)
- OpenAI API (AI summarization)
- Docker & Docker Compose

---

## Features

### 1. Automatic Data Fetching

- Fetches articles from NYT API
- Runs every 6 hours using APScheduler
- Prevents duplicate inserts using unique `external_id`

---

### 2. REST Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `api/articles/` | Paginated list of articles |
| GET | `api/articles/{id}/` | Article details |
| GET | `api/articles/{id}/summary/` | AI-generated summary |
| GET | `/health/` | Health check endpoint |

---

### 3. AI Summarization

- Uses OpenAI `gpt-4o-mini`
- Generates concise summaries (3–4 sentences)
- Uses article title, author, section and abstract
- Avoids repeated AI calls using caching

---

### 4. Caching Strategy

- Article list cached in Redis
- Article summaries cached in Redis
- TTL: 6 hours
- Cache invalidation on create/update/delete
- Prevents unnecessary database and AI calls

---

### 5. API Documentation (OpenAPI / Swagger)

The project uses **drf-spectacular** to generate OpenAPI documentation.

- OpenAPI Schema:
  `GET /schema/`

- Swagger UI:
  http://localhost:8000/docs/

- ReDoc:
  http://localhost:8000/redoc/

  - Articles API:
   http://localhost:8000/api/article/

---

## Technologies Used

- Python 3.9
- Django 4.2
- Django REST Framework
- PostgreSQL
- Redis
- OpenAI API
- APScheduler
- drf-spectacular
- Docker & Docker Compose

---

## Running the Project

### 1. Clone Repository

```bash
git clone <repo_url>
cd project_folder

Ceate file .env in the project root
DJANGO_SECRET_KEY=your_secret_key
OPENAI_API_KEY=your_openai_key
NYT_API_KEY=your_nyt_key

ENABLE_SCHEDULER=True

DB_HOST=db

DB_NAME=devdb

DB_USER=devuser

DB_PASS=changeme

REDIS_HOST=redis


build and start containers
docker-compose up --build

fetch article manually to tirst fetch

docker compose exec app python manage.py fetch_nyt

Access the Application

API Base URL:
http://localhost:8000/api/articles/

Swagger UI:
http://localhost:8000/schema/docs/

Health Check:
http://localhost:8000/health/