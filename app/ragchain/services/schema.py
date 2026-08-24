from pydantic import BaseModel


class ChatAttachment(BaseModel):
    filename: str
    text: str


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    allowed_categories: list[str] | None = None
    is_admin: bool = False
    attachment: ChatAttachment | None = None
