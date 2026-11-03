// Contrato de ChannelChips (Fase D): barra horizontal de chips de
// canal del overview de parsed-picks — "Todos" + un chip por canal
// con informanteId; activo según selectedId.
import React from "react";
import { render, fireEvent } from "@testing-library/react-native";
import ChannelChips, { ChannelChip } from "@/src/components/channel-chips";

const groups: ChannelChip[] = [
  { key: "canal-a", informanteId: 1, name: "Canal A" },
  { key: "canal-b", informanteId: 2, name: "Canal B" },
  { key: "sin-id", informanteId: null, name: "Sin ID" },
];

describe("ChannelChips", () => {
  it("renderiza Todos + chips con informanteId (sin-id no sale)", async () => {
    const s = await render(
      <ChannelChips groups={groups} selectedId={null} onSelect={jest.fn()} />
    );
    expect(s.getByText("Todos")).toBeTruthy();
    expect(s.getByText("Canal A")).toBeTruthy();
    expect(s.getByText("Canal B")).toBeTruthy();
    expect(s.queryByText("Sin ID")).toBeNull();
  });

  it("pulsar Todos emite null y pulsar un chip emite id+name", async () => {
    const onSelect = jest.fn();
    const s = await render(
      <ChannelChips groups={groups} selectedId={1} onSelect={onSelect} />
    );
    await fireEvent.press(s.getByText("Todos"));
    expect(onSelect).toHaveBeenCalledWith(null);
    await fireEvent.press(s.getByText("Canal B"));
    expect(onSelect).toHaveBeenCalledWith({ id: 2, name: "Canal B" });
  });
});
