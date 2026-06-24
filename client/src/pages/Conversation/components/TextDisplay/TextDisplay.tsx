import { FC, useEffect, useRef } from "react";
import { Turn } from "../../hooks/useTranscript";

type TextDisplayProps = {
  containerRef: React.RefObject<HTMLDivElement>;
  turns: Turn[];
};

export const TextDisplay: FC<TextDisplayProps> = ({ containerRef, turns }) => {
  const prevScrollTop = useRef(0);

  useEffect(() => {
    if (containerRef.current) {
      prevScrollTop.current = containerRef.current.scrollTop;
      containerRef.current.scroll({
        top: containerRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [turns]);

  return (
    <div className="h-full w-full max-w-full max-h-full p-2 space-y-2 text-left">
      {turns.map((turn, i) => (
        <p key={i} className={turn.speaker === "You" ? "text-blue-700" : "text-black"}>
          <span className="font-semibold">{turn.speaker}: </span>
          {turn.text}
        </p>
      ))}
    </div>
  );
};
