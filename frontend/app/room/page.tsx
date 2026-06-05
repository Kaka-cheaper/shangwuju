import { Suspense } from "react";

import RoomClient, { RoomShell } from "./RoomClient";

export default function RoomQueryPage() {
  return (
    <Suspense
      fallback={
        <RoomShell>
          <p className="text-ink-400 text-sm">正在加入房间...</p>
        </RoomShell>
      }
    >
      <RoomClient />
    </Suspense>
  );
}
