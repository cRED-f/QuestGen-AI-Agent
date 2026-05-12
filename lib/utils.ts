/* eslint-disable @typescript-eslint/no-explicit-any */
import { ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const logMessageEvent = (
  response: {
    event: string;
    data: any;
  },
  extra: {
    toolCallLogged: boolean;
    contentLogged: boolean;
  }
) => {
  const { event, data } = response;
  let { toolCallLogged, contentLogged } = extra;
  if (
    !Array.isArray(data) ||
    data.length === 0 ||
    !("content" in data[0] || "type" in data[0])
  ) {
    console.log("Non message event:", event);
    console.log("\n---\n");
  } else {
    data.forEach((msg: any) => {
      if (!msg.content && msg.tool_calls?.length) {
        if (!toolCallLogged) {
          console.log("\n---TOOL CALL---\n");
          toolCallLogged = true;
        }
        console.dir({
          type: msg.type,
          tool_calls: msg.tool_calls[0],
        });
      } else if (msg.content && msg.tool_calls?.length) {
        if (!toolCallLogged) {
          console.log("\n---TOOL CALL---\n");
          toolCallLogged = true;
        }
        if (!contentLogged) {
          console.log("\n---CONTENT---\n");
          contentLogged = true;
        }
        console.dir({
          type: msg.type,
          content: msg.content,
          tool_calls: msg.tool_calls[0],
        });
      } else if (msg.content) {
        if (!contentLogged) {
          console.log("\n---CONTENT---\n");
          contentLogged = true;
        }
        console.dir({
          type: msg.type,
          content: msg.content,
        });
      }
    });
    console.log("\n---\n");
  }

  return {
    toolCallLogged,
    contentLogged,
  };
};
