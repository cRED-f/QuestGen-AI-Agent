"use client";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export const MainMenusGradientCard = ({
  title,
  description,
  className,
  children,
}: {
  title: string;
  description: string;
  children?: ReactNode;
  className?: string;
}) => {
  return (
    <div className="overflow-y-scroll overflow-x-hidden overflow-scrollbar-hidden max-h-[80vh] rounded-[20px] bg-neutral-900/80 p-2 shadow-lg border border-neutral-800 relative">
      {/* Title and description header */}
      <div
        className={cn(
          "sticky top-0 z-10 h-auto py-3 text-slate-200 rounded-[15px] border-neutral-950 bg-gray-600/10",
          className
        )}
      >
        <div className="px-4">
          <h3 className="font-semibold text-neutral-300">{title}</h3>
          <p className="mt-1 text-neutral-400 truncate">{description}</p>
        </div>
      </div>

      {/* Children content body */}
      <div className="relative px-4 pb-6 pt-4 text-white">{children}</div>
    </div>
  );
};
