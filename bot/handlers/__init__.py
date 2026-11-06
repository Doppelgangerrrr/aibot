from aiogram import Router

from . import admin, user


def build_router() -> Router:
    root = Router(name="root")
    # админский роутер первым: ответы операторов и команды не должны уходить в ИИ-чат
    root.include_routers(admin.router, user.router)
    return root
