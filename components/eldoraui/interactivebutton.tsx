import React from "react";
import { ArrowRight } from "lucide-react";

interface InteractiveHoverButtonProps {
  text?: string;
  className?: string;
}

export function InteractiveHoverButton({
  text = "Start For Free",
  className,
}: InteractiveHoverButtonProps = {}) {
  return (
    <div
      className={`group relative w-[38vh] cursor-pointer overflow-hidden rounded-full  bg-white p-2 text-center font-semibold text-black ${className}`}
    >
      <span className="inline-block  text-2xl translate-x-1 transition-all duration-300 group-hover:translate-x-12 group-hover:opacity-0">
        {text}
      </span>
      <div className="absolute top-0 z-10  flex h-full w-full translate-x-12 items-center justify-center gap-2 text-white opacity-0 transition-all duration-300 group-hover:-translate-x-1 group-hover:opacity-100">
        <span>{text}</span>
        <ArrowRight />
      </div>
      <div className="absolute left-[6%] top-[40%] h-2 w-2 scale-[1] rounded-lg  transition-all duration-300 group-hover:left-[0%] group-hover:top-[0%] group-hover:h-full group-hover:w-full group-hover:scale-[1.8] group-hover:bg-gradient-to-br group-hover:from-purple-500 group-hover:to-blue-400 "></div>
    </div>
  );
}
