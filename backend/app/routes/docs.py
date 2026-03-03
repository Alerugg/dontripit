from flask import Blueprint, jsonify

docs_bp = Blueprint("docs", __name__)


@docs_bp.get("/api/openapi.json")
def openapi_spec():
    return jsonify(
        {
            "openapi": "3.0.0",
            "info": {"title": "TCG API", "version": "1.0.0"},
            "paths": {
                "/api/games": {"get": {"summary": "List games"}},
                "/api/cards": {"get": {"summary": "List cards"}},
                "/api/prints": {"get": {"summary": "List prints"}},
                "/api/prints/{id}": {"get": {"summary": "Get print details"}},
                "/api/sets": {"get": {"summary": "List sets"}},
                "/api/search": {"get": {"summary": "Search cards/prints/sets"}},
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
def docs_page():
    return """<!doctype html><html><body><h1>API Docs</h1><p>Use <code>/api/openapi.json</code>.</p></body></html>"""
