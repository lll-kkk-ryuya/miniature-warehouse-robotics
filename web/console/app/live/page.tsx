import { MapView2D } from "@/components/MapView2D";
import { SituationFleet } from "@/components/SituationFleet";
import { ConversationTimeline } from "@/components/ConversationTimeline";
import { RingiFlow } from "@/components/RingiFlow";
import { CommanderDecision } from "@/components/CommanderDecision";
import { EmergencyPanel } from "@/components/EmergencyPanel";
import { ModeGate } from "@/components/ModeGate";

// /live single screen (doc22:329). 3 columns: spatial/state | conversation+ringi (Mode-gated) |
// commander+emergency. In Mode C <ModeGate> drops the middle column and the others reflow.
export default function LivePage() {
  return (
    <main className="flex min-h-0 w-full gap-2 p-2">
      <div className="flex w-[26%] min-w-[14rem] flex-col gap-2">
        <div className="min-h-0 flex-1">
          <MapView2D />
        </div>
        <div className="min-h-0 flex-1">
          <SituationFleet />
        </div>
      </div>

      <ModeGate>
        <div className="flex w-[40%] flex-col gap-2">
          <div className="min-h-0 flex-[3]">
            <ConversationTimeline />
          </div>
          <div className="min-h-0 flex-[2]">
            <RingiFlow />
          </div>
        </div>
      </ModeGate>

      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="min-h-0 flex-[3]">
          <CommanderDecision />
        </div>
        <div className="min-h-0 flex-[2]">
          <EmergencyPanel />
        </div>
      </div>
    </main>
  );
}
