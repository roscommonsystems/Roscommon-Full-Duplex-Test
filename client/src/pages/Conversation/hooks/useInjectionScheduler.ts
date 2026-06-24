import { useEffect, useRef } from "react";
import { Injection } from "../../Queue/hooks/useScenarios";
import { planInjections } from "./injectionSchedule";
import { SocketStatus } from "../../../protocol/types";

export const useInjectionScheduler = ({
  socket,
  socketStatus,
  injections,
}: {
  socket: WebSocket | null;
  socketStatus: SocketStatus;
  injections?: Injection[];
}) => {
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    if (socketStatus !== "connected" || !socket || !injections || injections.length === 0) {
      return;
    }
    const planned = planInjections(injections);
    planned.forEach(({ delayMs, text }) => {
      const id = setTimeout(() => {
        try {
          socket.send(JSON.stringify({ type: "context", text }));
        } catch (e) {
          console.error("injection send failed", e);
        }
      }, delayMs);
      timers.current.push(id);
    });
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, [socket, socketStatus, injections]);
};
