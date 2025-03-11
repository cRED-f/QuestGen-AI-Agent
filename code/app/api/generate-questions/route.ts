import { NextRequest, NextResponse } from "next/server";
import { generateQuestions } from "@/services/index";
import fs from "fs";
import path from "path";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const uploadedFiles: string[] = [];

    // Extract files from form data
    for (const entry of formData.entries()) {
      const [key, value] = entry as [string, File];

      if (key.startsWith("file-") && value instanceof File) {
        // Create buffer from file data
        const buffer = Buffer.from(await value.arrayBuffer());

        // Ensure temp directory exists
        const tempDir = path.join(process.cwd(), "temp");
        if (!fs.existsSync(tempDir)) {
          fs.mkdirSync(tempDir, { recursive: true });
        }

        // Generate unique filename
        const timestamp = Date.now();
        const ext = path.extname(value.name);
        const filename = `${timestamp}${ext}`;
        const filePath = path.join(tempDir, filename);

        // Save file to temp directory
        fs.writeFileSync(filePath, buffer);
        uploadedFiles.push(filename);
      }
    }

    // Log uploaded files to verify
    console.log("Files uploaded via POST:", uploadedFiles);

    // Return the uploaded filenames in the response
    return NextResponse.json(
      {
        message: "success",
        uploadedFiles: uploadedFiles,
      },
      { status: 200 }
    );
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (error: any) {
    console.error("Error processing uploads:", error);
    return NextResponse.json(
      { message: error.message || "Failed to process request" },
      { status: 500 }
    );
  }
}

export async function GET(req: NextRequest) {
  try {
    const url = new URL(req.url);
    const questionHeader = url.searchParams.get("questionHeader");
    const questionDescription = url.searchParams.get("questionDescription");
    const apiKey = url.searchParams.get("apiKey");
    const modelName = url.searchParams.get("modelName") || "qwen/qwq-32b:free";
    const uploadedFilesParam = url.searchParams.get("uploadedFiles");

    // Validate required parameters
    if (!questionHeader || !questionDescription || !apiKey) {
      return NextResponse.json(
        {
          message:
            "Missing required parameters. Please provide questionHeader, questionDescription, and apiKey.",
          required: ["questionHeader", "questionDescription", "apiKey"],
          received: {
            questionHeader: !!questionHeader,
            questionDescription: !!questionDescription,
            apiKey: !!apiKey,
            modelName: !!modelName,
          },
        },
        { status: 400 }
      );
    }

    // Process uploaded files
    let uploadedFiles: string[] = [];
    if (uploadedFilesParam) {
      uploadedFiles = uploadedFilesParam
        .split(",")
        .filter((file) => file.trim() !== "");
    }

    console.log("Processing GET request with files:", uploadedFiles);

    // Check if we have files to process
    if (uploadedFiles.length === 0) {
      console.warn("No uploaded files found for processing");
    } else {
      // Verify files exist on disk
      const tempDir = path.join(process.cwd(), "temp");
      const existingFiles = uploadedFiles.filter((file) =>
        fs.existsSync(path.join(tempDir, file))
      );

      console.log(
        `Found ${existingFiles.length}/${uploadedFiles.length} files on disk`
      );

      if (existingFiles.length === 0 && uploadedFiles.length > 0) {
        return NextResponse.json(
          {
            message:
              "Uploaded files not found on server. Please try uploading again.",
          },
          { status: 400 }
        );
      }

      // Use only existing files
      uploadedFiles = existingFiles;
    }

    // Use the service to generate questions with streaming
    const result = await generateQuestions({
      questionHeader,
      questionDescription,
      apiKey,
      uploadedFiles,
      modelName,
    });

    // Delete PDF files after data extraction
    const tempDir = path.join(process.cwd(), "temp");
    for (const file of uploadedFiles) {
      try {
        const filePath = path.join(tempDir, file);
        if (fs.existsSync(filePath)) {
          fs.unlinkSync(filePath);
          console.log(`Deleted processed file: ${file}`);
        }
      } catch (deleteError) {
        console.error(`Error deleting file ${file}:`, deleteError);
      }
    }

    if (result.stream) {
      // Create a transform stream to handle the SSE data
      const { readable, writable } = new TransformStream();
      const writer = writable.getWriter();
      const encoder = new TextEncoder();

      // Process the stream in the background
      (async () => {
        try {
          for await (const event of result.stream) {
            console.log("Received event:", event);

            // Check if this is an agent type we want to process
            const agentType = Object.keys(event)[0];

            // Only process events from Formatter agent
            if (agentType !== "Formatter") {
              console.log(`Skipping event from agent: ${agentType}`);
              continue; // Skip this event
            }

            let content = "";

            try {
              // Process events from specified agents
              if (
                event[agentType] &&
                event[agentType].messages &&
                event[agentType].messages[0] &&
                event[agentType].messages[0].content
              ) {
                // Extract content from the standard messages structure
                content = event[agentType].messages[0].content;

                // If there's additional data like analysisResult, prefer it
                if (event[agentType].analysisResult) {
                  content = event[agentType].analysisResult;
                }
              } else {
                // Fallback to stringifying the entire event
                content = JSON.stringify(event);
              }

              console.log(`Processing content from ${agentType}:`, content);
              // eslint-disable-next-line @typescript-eslint/no-unused-vars
            } catch (err) {
              // If any error in parsing, use the event as is
              content = JSON.stringify(event);
            }

            // Clean up the content - remove code block formatting
            let cleanContent = content;

            // Remove markdown code block formatting if present
            cleanContent = cleanContent
              .replace(/```(json)?\n/g, "")
              .replace(/```$/g, "");

            // Check if content is JSON (starts with { or [)
            const isJsonContent =
              cleanContent.trim().startsWith("{") ||
              cleanContent.trim().startsWith("[");

            let messageToSend;

            if (isJsonContent) {
              // If it's JSON content, send busy server message
              messageToSend = "server is busy currently try again later";
              console.log("JSON content detected, sending busy server message");

              // Format as proper SSE with a consistent delimiter
              const formattedChunk = `data: ${JSON.stringify({
                type: "error",
                content: messageToSend,
              })}\n\n`;

              // Write the chunk to the stream
              await writer.write(encoder.encode(formattedChunk));

              // Close the stream after sending error
              await writer.write(
                encoder.encode("event: complete\ndata: done\n\n")
              );
              await writer.close();

              // Exit the processing loop
              return;
            } else {
              // For markdown content, prepare it for sending
              messageToSend = cleanContent;
              const formattedChunk = `data: ${JSON.stringify({
                type: "markdown",
                content: messageToSend,
                isMarkdown: true,
              })}\n\n`;

              console.log(
                `Sending markdown chunk (length: ${formattedChunk.length})`
              );

              // Write the chunk to the stream
              await writer.write(encoder.encode(formattedChunk));
            }
          }

          // Signal completion
          await writer.write(encoder.encode("event: complete\ndata: done\n\n"));
          await writer.close();
        } catch (error: unknown) {
          console.error("Stream error:", error);
          const errorMessage =
            error instanceof Error
              ? error.message
              : "An unknown error occurred";
          await writer.write(
            encoder.encode(
              `event: error\ndata: ${JSON.stringify({
                error: errorMessage,
              })}\n\n`
            )
          );
          await writer.close();
        }
      })();

      // Return the readable side of the transform stream as the response
      return new Response(readable, {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
      });
    } else {
      return NextResponse.json(
        { message: "No stream returned from generation service" },
        { status: 500 }
      );
    }
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } catch (error: any) {
    console.error("Error generating questions:", error);
    return NextResponse.json(
      { message: error.message || "Failed to generate questions" },
      { status: 500 }
    );
  }
}
