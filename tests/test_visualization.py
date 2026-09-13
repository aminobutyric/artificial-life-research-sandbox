import asyncio

import httpx

from agentic_simulation.config import (
    AppConfig,
    ResourceConfig,
    ResourcesConfig,
    WorldConfig,
)
from agentic_simulation.visualization import create_app


def test_visualization_and_controls() -> None:
    config = AppConfig(
        world=WorldConfig(
            width=12,
            height=10,
            resources=ResourcesConfig(
                food=ResourceConfig(initial_nodes=3, capacity=10),
                water=ResourceConfig(initial_nodes=2, capacity=10),
            ),
        )
    )

    async def exercise_api() -> None:
        app = create_app(config)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            initial = (await client.get("/api/world")).json()
            assert initial["tick"] == 0
            assert len(initial["layers"]["terrain"]) == 120

            stepped = (await client.post("/api/control/step")).json()
            assert stepped["tick"] == 1

            regenerated = (
                await client.post("/api/control/regenerate", json={"seed": 99})
            ).json()
            assert regenerated["tick"] == 0
            assert regenerated != initial

    asyncio.run(exercise_api())
