import asyncio
from backend.app.agents.base_agent import BaseAgent


class T(BaseAgent):
    agent_name = "t"

    async def execute(self, s):
        return s


async def main():
    t = T()
    print("provider =", t.provider)
    print("url      =", t.base_url)
    print("model    =", t.model)
    print("has_key  =", bool(t.api_key), "(len", len(t.api_key), ")")
    try:
        txt, i, o = await t.call_llm(
            'You are a JSON robot. Reply with only: {"ok":true}',
            "ping",
            max_tokens=64,
        )
        print("RESPONSE:", txt)
        print("tokens  :", i, o)
    except Exception as e:
        import traceback
        print("ERROR   :", type(e).__name__, str(e)[:500])
        # If retryable wrapped an HTTPStatusError, surface its response body
        for cause in (getattr(e, "__cause__", None), getattr(e, "last_attempt", None)):
            if cause is None:
                continue
            inner = cause.exception() if hasattr(cause, "exception") else cause
            resp = getattr(inner, "response", None)
            if resp is not None:
                print("STATUS  :", resp.status_code)
                print("BODY    :", resp.text[:800])
        traceback.print_exc()


asyncio.run(main())
