from pydantic import BaseModel


class EditItem(BaseModel):
    row_key: int
    field: str
    value: str


class SubmitRequest(BaseModel):
    edits: list[EditItem] = []
    force_advance: bool = False


class DraftRequest(BaseModel):
    edits: list[EditItem] = []
