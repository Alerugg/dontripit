from app.scripts.seed import run_seed


if __name__ == "__main__":
    created = run_seed()
    print(f"seed complete; inserted={created}")
