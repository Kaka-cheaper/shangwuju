import { Suspense } from "react";

import RoomClient, { RoomShell } from "../RoomClient";

interface RoomPathPageProps {
  params: {
    id: string;
  };
}

export function generateStaticParams() {
  return [{ id: "demo" }];
}

export default function RoomPathPage({ params }: RoomPathPageProps) {
  return (
    <Suspense
      fallback={
        <RoomShell>
          <p className="text-ink-400 text-sm">正在加入房间...</p>
        </RoomShell>
      }
    >
      <RoomClient roomIdFromPath={params.id} />
    </Suspense>
  );
}
