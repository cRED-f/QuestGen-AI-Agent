"use client";
import React from "react";

import fahim from "../app/assets/img/fahim.jpg";

import { AnimatedTooltip } from "./ui/animated-tooltip";
const people = [
  {
    id: 1,
    name: "Fahim",
    designation: "https://github.com/cRED-f",
    image: fahim.src,
  },
];

export function About() {
  return (
    <div className="flex flex-row items-center justify-center mb-10 w-full">
      <AnimatedTooltip items={people} />
    </div>
  );
}
