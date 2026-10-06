/** The recorded engine run (tests/fixtures/record_fixture.py) for unit tests. */
import raw from "../../tests/fixtures/terwillegar_grass_4h.json?raw";
import type { SimulationResponse } from "../types/simulation";

export const fixture = JSON.parse(raw) as SimulationResponse;
