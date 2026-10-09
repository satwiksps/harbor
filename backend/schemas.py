from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = {"extra": "forbid"}


def clean_email(value):
    value = value.strip().lower()
    if len(value) > 254 or value.count("@") != 1 or "." not in value.split("@")[-1]:
        raise ValueError("Enter a valid email address")
    return value


def clean_name(value):
    if value is None:
        return value
    value = value.strip()
    if not value:
        raise ValueError("A name cannot be blank")
    return value


class LoginBody(Model):
    email: str
    password: str = Field(min_length=1, max_length=256)
    normalize_email = field_validator("email")(clean_email)


class AcceptInvite(Model):
    token: str = Field(max_length=128)
    name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=12, max_length=256)
    normalize_name = field_validator("name")(clean_name)


class NewInvite(Model):
    email: str
    role: Literal["admin", "member"] = "member"
    normalize_email = field_validator("email")(clean_email)


class MemberUpdate(Model):
    role: Literal["admin", "member"] | None = None
    active: bool | None = None


class NewKey(Model):
    name: str = Field(min_length=1, max_length=100)
    user_id: str | None = None
    normalize_name = field_validator("name")(clean_name)


class ServerBody(Model):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{0,23}$")
    url: str = Field(min_length=1, max_length=2048)
    auth_type: Literal["none", "bearer", "header"] = "none"
    credential: str | None = Field(default=None, max_length=8192)
    header_name: str | None = Field(default=None, max_length=100)
    enabled: bool = True
    normalize_name = field_validator("name")(clean_name)


class ServerUpdate(Model):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    auth_type: Literal["none", "bearer", "header"] | None = None
    credential: str | None = Field(default=None, max_length=8192)
    header_name: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None
    clear_credential: bool = False
    normalize_name = field_validator("name")(clean_name)


class PermissionUpdate(Model):
    permissions: dict[str, Literal["read", "write"]]


class ChatMessage(Model):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


class ChatBody(Model):
    messages: list[ChatMessage] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def cap_total(self):
        if sum(len(message.content) for message in self.messages) > 40000:
            raise ValueError("Conversation is too long; start a new chat")
        if self.messages[-1].role != "user":
            raise ValueError("The last message must be from the user")
        return self


class ConfirmBody(Model):
    id: str
    approve: bool
