from flask import Blueprint, Response, jsonify, redirect, render_template_string, send_from_directory, url_for
from swagger_ui_bundle import swagger_ui_path

docs_bp = Blueprint("docs", __name__)

_SWAGGER_UI_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Dontripit API Docs</title>
    <link rel="stylesheet" href="{{ assets_base }}/swagger-ui.css" />
    <link rel="icon" type="image/png" href="{{ assets_base }}/favicon-32x32.png" sizes="32x32" />
    <style>
      html { box-sizing: border-box; overflow-y: scroll; }
      *, *:before, *:after { box-sizing: inherit; }
      body { margin: 0; background: #fafafa; }
    </style>
  </head>
  <body>
    <div id="swagger-ui"></div>
    <script src="{{ assets_base }}/swagger-ui-bundle.js"></script>
    <script src="{{ assets_base }}/swagger-ui-standalone-preset.js"></script>
    <script>
      window.onload = () => {
        window.ui = SwaggerUIBundle({
          url: "{{ spec_url }}",
          dom_id: "#swagger-ui",
          deepLinking: true,
          presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
          layout: "StandaloneLayout",
          persistAuthorization: true,
        });
      };
    </script>
  </body>
</html>
"""


def _pagination_parameters(*, require_game: bool = False):
    parameters = [
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "minimum": 0, "maximum": 200, "default": 20},
            "description": "Maximum number of rows to return.",
        },
        {
            "name": "offset",
            "in": "query",
            "schema": {"type": "integer", "minimum": 0, "default": 0},
            "description": "Number of rows to skip before collecting the response page.",
        },
    ]
    parameters.insert(
        0,
        {
            "name": "game",
            "in": "query",
            "required": require_game,
            "schema": {"type": "string"},
            "description": "Game slug such as pokemon, mtg, onepiece, yugioh, or riftbound.",
        },
    )
    return parameters


def _openapi_document() -> dict:
    json_error = {
        "description": "Request failed",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ErrorResponse"},
            }
        },
    }
    auth_responses = {
        "401": {"description": "Missing or invalid API key", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
        "403": {"description": "Insufficient scope", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
        "429": {"description": "Rate limit or quota exceeded", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}}},
    }

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Dontripit Backend API",
            "version": "1.0.0",
            "description": "OpenAPI document served directly by the Flask backend. All protected endpoints accept X-API-Key or Bearer authentication.",
        },
        "servers": [
            {"url": "http://localhost:5000", "description": "Local backend via Docker or flask run"},
        ],
        "tags": [
            {"name": "System", "description": "Health and operational endpoints."},
            {"name": "Games", "description": "Game catalog endpoints."},
            {"name": "Search", "description": "Search and suggestions."},
            {"name": "Catalog", "description": "Cards, prints, sets, products, and variants."},
        ],
        "paths": {
            "/api/health": {
                "get": {
                    "tags": ["System"],
                    "summary": "Health check",
                    "description": "Returns backend liveness and revision metadata. This endpoint does not require authentication.",
                    "responses": {
                        "200": {
                            "description": "Backend is healthy",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}},
                        }
                    },
                }
            },
            "/api/games": {
                "get": {
                    "tags": ["Games"],
                    "summary": "List games",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "responses": {
                        "200": {
                            "description": "Visible games",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Game"}}}},
                        },
                        **auth_responses,
                    },
                }
            },
            "/api/v1/search": {
                "get": {
                    "tags": ["Search"],
                    "summary": "Search cards, prints, or sets",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1}, "description": "Search query."},
                        {"name": "game", "in": "query", "schema": {"type": "string"}, "description": "Optional game slug filter."},
                        {"name": "type", "in": "query", "schema": {"type": "string", "enum": ["card", "print", "set"]}, "description": "Restrict result type."},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}, "description": "Result page size; max depends on query length."},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0, "default": 0}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Search results",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/SearchResult"}}}},
                        },
                        "400": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/search/suggest": {
                "get": {
                    "tags": ["Search"],
                    "summary": "Autocomplete suggestions",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string", "minLength": 1}},
                        {"name": "game", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 10}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Suggestion rows",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/SearchResult"}}}},
                        },
                        **auth_responses,
                    },
                }
            },
            "/api/v1/sets": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "List sets",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": _pagination_parameters(require_game=True) + [
                        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Set name or code filter."},
                    ],
                    "responses": {
                        "200": {
                            "description": "Set list",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/Set"}}}},
                        },
                        "400": json_error,
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/cards": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "List cards",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": _pagination_parameters(require_game=True) + [
                        {"name": "q", "in": "query", "schema": {"type": "string"}, "description": "Card name filter."},
                    ],
                    "responses": {
                        "200": {
                            "description": "Card list",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/CardSummary"}}}},
                        },
                        "400": json_error,
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/cards/{card_id}": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "Get card detail",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "card_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Card detail with prints and sets",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CardDetail"}}},
                        },
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/prints": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "List prints",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": _pagination_parameters(require_game=True) + [
                        {"name": "set_code", "in": "query", "schema": {"type": "string"}},
                        {"name": "card_id", "in": "query", "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Print list",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/PrintSummary"}}}},
                        },
                        "400": json_error,
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/prints/{print_id}": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "Get print detail",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "print_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Print detail",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PrintDetail"}}},
                        },
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/products": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "List catalog products",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "game", "in": "query", "schema": {"type": "string"}},
                        {"name": "set_code", "in": "query", "schema": {"type": "string"}},
                        {"name": "type", "in": "query", "schema": {"type": "string"}},
                        {"name": "q", "in": "query", "schema": {"type": "string"}},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 0, "maximum": 100, "default": 20}},
                        {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0, "default": 0}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Product search response",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProductListResponse"}}},
                        },
                        **auth_responses,
                    },
                }
            },
            "/api/v1/products/{product_id}": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "Get product detail",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "product_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Product detail",
                            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProductDetailResponse"}}},
                        },
                        "404": json_error,
                        **auth_responses,
                    },
                }
            },
            "/api/v1/product-variants": {
                "get": {
                    "tags": ["Catalog"],
                    "summary": "List product variants",
                    "security": [{"ApiKeyHeader": []}, {"BearerAuth": []}],
                    "parameters": [
                        {"name": "product_id", "in": "query", "required": True, "schema": {"type": "integer"}},
                    ],
                    "responses": {
                        "200": {
                            "description": "Product variants for a product",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"$ref": "#/components/schemas/ProductVariant"}}}},
                        },
                        "400": json_error,
                        **auth_responses,
                    },
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            },
            "schemas": {
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["error"],
                },
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "ok": {"type": "boolean"},
                        "revision": {"type": "string"},
                    },
                    "required": ["ok", "revision"],
                },
                "Game": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "slug": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "slug", "name"],
                },
                "Set": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "code": {"type": "string"},
                        "name": {"type": "string"},
                        "game_slug": {"type": "string"},
                        "tcgdex_id": {"type": ["string", "null"]},
                        "scryfall_id": {"type": ["string", "null"]},
                        "yugioh_id": {"type": ["string", "null"]},
                        "riftbound_id": {"type": ["string", "null"]},
                    },
                    "required": ["id", "code", "name", "game_slug"],
                },
                "CardSummary": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "game_slug": {"type": "string"},
                        "tcgdex_id": {"type": ["string", "null"]},
                        "scryfall_id": {"type": ["string", "null"]},
                        "yugioh_id": {"type": ["string", "null"]},
                        "riftbound_id": {"type": ["string", "null"]},
                    },
                    "required": ["id", "name", "game_slug"],
                },
                "CardExternalIds": {
                    "type": "object",
                    "properties": {
                        "tcgdex_id": {"type": ["string", "null"]},
                        "scryfall_id": {"type": ["string", "null"]},
                        "yugioh_id": {"type": ["string", "null"]},
                        "riftbound_id": {"type": ["string", "null"]},
                    },
                },
                "PrintSummary": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "set_code": {"type": "string"},
                        "card_id": {"type": "integer"},
                        "collector_number": {"type": ["string", "null"]},
                        "language": {"type": ["string", "null"]},
                        "rarity": {"type": ["string", "null"]},
                        "is_foil": {"type": ["boolean", "null"]},
                        "variant": {"type": ["string", "null"]},
                        "image_url": {"type": ["string", "null"]},
                        "primary_image_url": {"type": ["string", "null"]},
                    },
                    "required": ["id", "set_code", "card_id"],
                },
                "CardDetail": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "game_slug": {"type": "string"},
                        "game": {"type": "string"},
                        "primary_image_url": {"type": ["string", "null"]},
                        "external_ids": {"$ref": "#/components/schemas/CardExternalIds"},
                        "prints": {"type": "array", "items": {"$ref": "#/components/schemas/PrintSummary"}},
                        "sets": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "integer"}, "code": {"type": "string"}, "name": {"type": "string"}}, "required": ["id", "code", "name"]}},
                    },
                    "required": ["id", "name", "game_slug", "game", "external_ids", "prints", "sets"],
                },
                "PrintDetail": {
                    "type": "object",
                    "description": "Detailed print payload as returned by the backend.",
                    "additionalProperties": True,
                },
                "SearchResult": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "id": {"type": "integer"},
                        "card_id": {"type": ["integer", "null"]},
                        "title": {"type": ["string", "null"]},
                        "subtitle": {"type": ["string", "null"]},
                        "game": {"type": ["string", "null"]},
                        "set_code": {"type": ["string", "null"]},
                        "set_name": {"type": ["string", "null"]},
                        "collector_number": {"type": ["string", "null"]},
                        "language": {"type": ["string", "null"]},
                        "variant": {"type": ["string", "null"]},
                        "variant_count": {"type": ["number", "null"]},
                        "primary_image_url": {"type": ["string", "null"]},
                    },
                    "required": ["type", "id"],
                },
                "ProductSummary": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "product_type": {"type": "string"},
                        "name": {"type": "string"},
                        "release_date": {"type": ["string", "null"], "format": "date"},
                        "game": {"type": "string"},
                        "set_code": {"type": ["string", "null"]},
                        "variant_count": {"type": "integer"},
                        "primary_image_url": {"type": ["string", "null"]},
                    },
                    "required": ["id", "product_type", "name", "game", "variant_count"],
                },
                "ProductListResponse": {
                    "type": "object",
                    "properties": {
                        "items": {"type": "array", "items": {"$ref": "#/components/schemas/ProductSummary"}},
                        "limit": {"type": "integer"},
                        "offset": {"type": "integer"},
                        "total": {"type": "integer"},
                    },
                    "required": ["items", "limit", "offset", "total"],
                },
                "ProductVariant": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "product_id": {"type": "integer"},
                        "language": {"type": ["string", "null"]},
                        "region": {"type": ["string", "null"]},
                        "packaging": {"type": ["string", "null"]},
                        "sku": {"type": ["string", "null"]},
                    },
                    "required": ["id", "product_id"],
                },
                "ProductImage": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "product_variant_id": {"type": "integer"},
                        "url": {"type": "string"},
                        "is_primary": {"type": ["boolean", "null"]},
                        "source": {"type": ["string", "null"]},
                    },
                    "required": ["id", "product_variant_id", "url"],
                },
                "ProductIdentifier": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "product_variant_id": {"type": "integer"},
                        "source": {"type": "string"},
                        "external_id": {"type": "string"},
                    },
                    "required": ["id", "product_variant_id", "source", "external_id"],
                },
                "ProductDetailResponse": {
                    "type": "object",
                    "properties": {
                        "product": {"type": "object", "additionalProperties": True},
                        "variants": {"type": "array", "items": {"$ref": "#/components/schemas/ProductVariant"}},
                        "images": {"type": "array", "items": {"$ref": "#/components/schemas/ProductImage"}},
                        "identifiers": {"type": "array", "items": {"$ref": "#/components/schemas/ProductIdentifier"}},
                    },
                    "required": ["product", "variants", "images", "identifiers"],
                },
            },
        },
    }


@docs_bp.get("/openapi.json")
@docs_bp.get("/api/openapi.json")
@docs_bp.get("/api/v1/openapi.json")
def openapi_spec():
    return jsonify(_openapi_document())


@docs_bp.get("/docs")
def docs_index():
    return redirect(url_for("docs.docs_ui"), code=302)


@docs_bp.get("/docs/")
@docs_bp.get("/api/docs")
@docs_bp.get("/api/v1/docs")
def docs_ui():
    return Response(
        render_template_string(
            _SWAGGER_UI_HTML,
            spec_url=url_for("docs.openapi_spec"),
            assets_base=url_for("docs.docs_assets", filename="").rstrip("/"),
        ),
        mimetype="text/html",
    )


@docs_bp.get("/docs/assets/<path:filename>")
def docs_assets(filename: str):
    return send_from_directory(swagger_ui_path, filename)
