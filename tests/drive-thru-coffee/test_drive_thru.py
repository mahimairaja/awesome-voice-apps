from livekit.agents import AgentSession
from livekit.agents.evals import (
    JudgeGroup,
    relevancy_judge,
    task_completion_judge,
    tool_use_judge,
)
from livekit.plugins import openai

from agent import DriveThruAttendant


async def test_takes_and_submits_an_order(fake_room, judge_llm):
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata={"cart": []}) as session:
        await session.start(DriveThruAttendant(fake_room))

        await session.run(user_input="A large iced latte with oat milk, please.")
        await session.run(user_input="That is everything, and the name is Sam.")

        judges = JudgeGroup(
            llm=judge_llm,
            judges=[task_completion_judge(), tool_use_judge(), relevancy_judge()],
        )
        evaluation = await judges.evaluate(session.history)
        assert evaluation.all_passed, {
            name: j.reasoning for name, j in evaluation.judgments.items()
        }


def test_only_names_become_cup_names():
    from agent import _cup_name

    cases = {
        "Sam": "Sam",
        "Priya.": "Priya",
        "It's Sam.": "Sam",
        "The name is Mary Ann": "Mary Ann",
        "Jean-Luc": "Jean-Luc",
        "José": "José",
        "李": "李",
    }
    for reply, name in cases.items():
        assert _cup_name(reply) == name, reply
    for reply in ("No, that's everything.", "That's all, thanks.", "I'm done", "Yes", ""):
        assert _cup_name(reply) is None, reply


async def test_adds_items_as_soon_as_they_are_named(fake_room):
    # The first live eval confirmed the order out loud without adding it, then
    # called remove_item on an empty cart and dropped the size.
    async with AgentSession(llm=openai.LLM(model="gpt-4o-mini"), userdata={"cart": []}) as session:
        await session.start(DriveThruAttendant(fake_room))

        await session.run(user_input="Hi, can I get a large latte and a blueberry muffin?")

        cart = session.userdata["cart"]
        assert [item["key"] for item in cart] == ["latte", "blueberry_muffin"]
        assert "large" in " ".join(cart[0]["modifications"]).lower()


async def test_add_item_keeps_the_size_and_refuses_off_menu_keys(fake_room):
    from types import SimpleNamespace

    agent = DriveThruAttendant(fake_room)
    context = SimpleNamespace(userdata={"cart": []})

    reply = await agent.add_item(context, "iced_latte", ["large", "oat milk"])
    assert reply == "Added one iced latte with large, oat milk."
    assert context.userdata["cart"][0]["modifications"] == ["large", "oat milk"]
    assert await agent.add_item(context, "matcha_latte") == "I do not have that item."
    assert len(context.userdata["cart"]) == 1
