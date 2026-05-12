import React from "react";
import { ArrowLeft } from "lucide-react";

interface InteractiveHoverButtonProps {
  className?: string;
}

export function ArrowButton({ className }: InteractiveHoverButtonProps = {}) {
  return (
    <div
      className={`group relative w-12 h-12 cursor-pointer overflow-hidden rounded-full  bg-white p-2 text-center font-semibold text-black ${className}`}
    >
      <div className="absolute top-0 z-10  flex h-full w-full translate-x-12 items-center justify-center gap-2 text-white opacity-0 transition-all duration-300 group-hover:-translate-x-1 group-hover:opacity-100">
        <ArrowLeft />
      </div>
      <div className="absolute    scale-[1] rounded-lg  transition-all duration-300 group-hover:left-[0%] group-hover:top-[0%] group-hover:h-full group-hover:w-full group-hover:scale-[1.8] group-hover:bg-gradient-to-br group-hover:from-purple-500 group-hover:to-blue-400 ">
        {" "}
        <ArrowLeft />
      </div>
    </div>
  );
}
