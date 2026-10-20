from fastapi import FastAPI

app = FastAPI(title="Tickmark")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
