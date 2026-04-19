"""Tests for ragent.common.context module."""
import pytest

from ragent.common.context import (
    UserContext,
    UserContextManager,
    clear_user_context,
    get_current_user_id,
    get_user_context,
    set_user_context,
)


# ---------------------------------------------------------------------------
# UserContext dataclass
# ---------------------------------------------------------------------------

class TestUserContext:
    def test_creation_minimal(self):
        ctx = UserContext(user_id="u001", username="alice")
        assert ctx.user_id == "u001"
        assert ctx.username == "alice"
        assert ctx.role == "user"
        assert ctx.tenant_id is None
        assert ctx.extra == {}

    def test_creation_full(self):
        ctx = UserContext(
            user_id="u002",
            username="bob",
            role="admin",
            tenant_id="t001",
            extra={"foo": "bar"},
        )
        assert ctx.role == "admin"
        assert ctx.tenant_id == "t001"
        assert ctx.extra == {"foo": "bar"}


# ---------------------------------------------------------------------------
# set / get / clear functions
# ---------------------------------------------------------------------------

class TestContextFunctions:
    def test_set_and_get(self):
        ctx = UserContext(user_id="u001", username="alice")
        set_user_context(ctx)
        retrieved = get_user_context()
        assert retrieved is ctx
        assert retrieved.user_id == "u001"
        clear_user_context()

    def test_get_returns_none_when_unset(self):
        clear_user_context()
        assert get_user_context() is None

    def test_get_current_user_id(self):
        ctx = UserContext(user_id="u003", username="charlie")
        set_user_context(ctx)
        assert get_current_user_id() == "u003"
        clear_user_context()

    def test_get_current_user_id_none(self):
        clear_user_context()
        assert get_current_user_id() is None

    def test_clear(self):
        set_user_context(UserContext(user_id="u004", username="dave"))
        assert get_current_user_id() == "u004"
        clear_user_context()
        assert get_user_context() is None
        assert get_current_user_id() is None

    def test_overwrite(self):
        ctx1 = UserContext(user_id="u010", username="first")
        ctx2 = UserContext(user_id="u011", username="second")
        set_user_context(ctx1)
        assert get_current_user_id() == "u010"
        set_user_context(ctx2)
        assert get_current_user_id() == "u011"
        clear_user_context()


# ---------------------------------------------------------------------------
# UserContextManager (async context manager)
# ---------------------------------------------------------------------------

class TestUserContextManager:
    @pytest.mark.asyncio
    async def test_sets_and_clears_context(self):
        ctx = UserContext(user_id="u100", username="manager_test")
        async with UserContextManager(ctx):
            assert get_current_user_id() == "u100"
            assert get_user_context() is ctx
        # After exiting, context should be cleared
        assert get_user_context() is None
        assert get_current_user_id() is None

    @pytest.mark.asyncio
    async def test_clears_on_exception(self):
        ctx = UserContext(user_id="u200", username="exception_test")
        with pytest.raises(ValueError):
            async with UserContextManager(ctx):
                assert get_current_user_id() == "u200"
                raise ValueError("test")
        # Should still be cleared
        assert get_user_context() is None

    @pytest.mark.asyncio
    async def test_nested_managers(self):
        ctx1 = UserContext(user_id="outer", username="outer_user")
        ctx2 = UserContext(user_id="inner", username="inner_user")
        async with UserContextManager(ctx1):
            assert get_current_user_id() == "outer"
            async with UserContextManager(ctx2):
                assert get_current_user_id() == "inner"
            # After inner exits, it clears the context entirely
            # (ContextVar reset behavior)
        assert get_user_context() is None

    @pytest.mark.asyncio
    async def test_aenter_returns_self(self):
        ctx = UserContext(user_id="u300", username="return_test")
        async with UserContextManager(ctx) as manager:
            assert isinstance(manager, UserContextManager)
