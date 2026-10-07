"""Structured output of the repair step."""

from pydantic import BaseModel, Field


class FileEdit(BaseModel):
    path: str = Field(description="Project-relative path of a file listed as editable.")
    content: str = Field(description="The complete new content of the file.")


class RepairProposal(BaseModel):
    diagnosis: str = Field(description="Root cause of the failure, in one or two sentences.")
    can_repair: bool = Field(
        description="False if the fix needs a change that is not allowed; explain in diagnosis."
    )
    edits: list[FileEdit] = Field(
        default_factory=list, description="Smallest set of file changes that fixes the failure."
    )
