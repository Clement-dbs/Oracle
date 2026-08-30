import os

from dotenv import load_dotenv

load_dotenv()

# Redis
REDIS_URL = os.getenv("REDIS_URL")

# Vectorstore
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_COLLECTION = "documents"
VECTOR_SIZE = 1024
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDINGS_MODEL")
EMBEDDINGS_MODEL_LOCAL_PATH = os.getenv("EMBEDDINGS_MODEL_LOCAL_PATH")
RERANKER_MODEL = os.getenv("RERANKER_MODEL")
RERANKER_LOCAL_PATH = os.getenv("RERANKER_LOCAL_PATH")

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_HOST")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL")

# Minio
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_ROOT_USER = os.getenv("MINIO_ROOT_USER")
MINIO_ROOT_PASSWORD = os.getenv("MINIO_ROOT_PASSWORD")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_BUCKET = "oracle"
