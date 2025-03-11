import React from "react";
import { Integrations } from "@/components/eldoraui/integrations";
import { BackgroundBeamsWithCollision } from "@/components/ui/background-beams-with-collision";
import { InteractiveHoverButton } from "@/components/eldoraui/interactivebutton";
import { ColourfulText } from "@/components/ui/colourful-text";
import Link from "next/link";
import { About } from "@/components/About";

function page() {
  return (
    <BackgroundBeamsWithCollision className="h-screen">
      <div
        className="h-[60%] flex justify-center w-full  items-center space-x-12"
        id="left-and-right-comp"
      >
        <div id="left" className=" flex-col  w-full ">
          <div className="  py-10 px-10">
            <h1 className="text-5xl font-extrabold text-white">
              <ColourfulText text="QuestGen" />: AI-Powered Exam Question
              Generator
            </h1>
            <p className="mt-4 text-xl text-gray-200">
              Automate the creation of customized question papers based on
              user-uploaded PDFs
            </p>
            <Link href="/chat">
              <InteractiveHoverButton className=" my-8" />
            </Link>
          </div>
        </div>
        <div id="right" className="flex-1 w-[60%] ">
          <div className="relative z-10 h-[500px] w-[90%] overflow-hidden mx-auto rounded-lg bg-opacity-100 bg-background">
            <Integrations />
          </div>
        </div>
      </div>

      {/* Fixed footer at the bottom */}
      <div className="fixed  bottom-0 w-full flex items-center justify-center py-4  ">
        <div className="flex-col w-fit items-center mx-auto justify-end">
          <p className="text-center text-neutral-400 mb-2">Created By</p>
          <About />
        </div>
      </div>
    </BackgroundBeamsWithCollision>
  );
}

export default page;
