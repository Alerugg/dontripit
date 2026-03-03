from flask import Blueprint, jsonify

docs_bp = Blueprint("docs", __name__)


@docs_bp.get("/api/openapi.json")
@docs_bp.get("/api/v1/openapi.json")
def openapi_spec():
    return jsonify(
        {
            "openapi": "3.0.0",
            "info": {"title": "TCG API", "version": "1.0.0"},
            "paths": {
                "/api/v1/games": {"get": {"summary": "List games"}},
                "/api/v1/cards": {"get": {"summary": "List cards"}},
                "/api/v1/prints": {"get": {"summary": "List prints"}},
                "/api/v1/prints/{id}": {"get": {"summary": "Get print details"}},
                "/api/v1/sets": {"get": {"summary": "List sets"}},
                "/api/v1/search": {"get": {"summary": "Search cards/prints/sets"}},
                "/api/v1/admin/metrics": {"get": {"summary": "Admin API usage metrics"}},
            },
            "components": {
                "securitySchemes": {
                    "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                    "BearerAuth": {"type": "http", "scheme": "bearer"},
                }
            },
        }
    )


@docs_bp.get("/api/docs")
@docs_bp.get("/api/v1/docs")
def docs_page():
    return """<!doctype html><html><body><h1>API Docs</h1><p>Use <code>/api/v1/openapi.json</code>.</p></body></html>"""
