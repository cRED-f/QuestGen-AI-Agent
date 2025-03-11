import { NextRequest, NextResponse } from "next/server";
import { generateQuestions } from "@/services/index";

import { ConvexHttpClient } from "convex/browser";
import { api } from "@/convex/_generated/api";
import { Id } from "@/convex/_generated/dataModel";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const formData = await req.formData();
    const uploadedFileIds: string[] = [];

    // Initialize Convex client
    const convex = new ConvexHttpClient(
      process.env.NEXT_PUBLIC_CONVEX_URL || ""
    );

    // Verify Convex connection
    console.log("Convex URL:", process.env.NEXT_PUBLIC_CONVEX_URL);

    if (!process.env.NEXT_PUBLIC_CONVEX_URL) {
      throw new Error("Missing NEXT_PUBLIC_CONVEX_URL environment variable");
    }

    // Extract files from form data
    for (const entry of formData.entries()) {
      const [key, value] = entry as [string, File];

      if (key.startsWith("file-") && value instanceof File) {
        // Upload file to Convex
        try {
          console.log(
            `Processing file: ${value.name} (${value.type}), size: ${value.size} bytes`
          );

          // Get file buffer
          const buffer = Buffer.from(await value.arrayBuffer());
          console.log(`Created buffer of size: ${buffer.length} bytes`);

          // Sanitize filename to prevent issues with special characters
          const sanitizedName = value.name.replace(/[^a-zA-Z0-9.-]/g, "_");
          console.log(`Sanitized filename: ${sanitizedName}`);

          try {
            // Generate upload URL from Convex
            console.log("Requesting upload URL from Convex...");
            const uploadUrl = await convex.mutation(
              api.files.generateUploadUrl,
              {
                filename: sanitizedName,
                contentType: value.type || "application/pdf",
              }
            );
            console.log(
              "Upload URL received:",
              uploadUrl ? "Success" : "Failed"
            );

            if (!uploadUrl) {
              throw new Error("Failed to get upload URL from Convex");
            }

            // Upload file to the generated URL
            console.log("Uploading file to Convex storage...");
            const uploadResponse = await fetch(uploadUrl, {
              method: "POST",
              headers: {
                "Content-Type": value.type || "application/pdf",
              },
              body: buffer,
            });

            if (!uploadResponse.ok) {
              const responseText = await uploadResponse.text();
              console.error("Upload response:", responseText);
              throw new Error(
                `Upload failed with status ${uploadResponse.status}: ${responseText}`
              );
            }

            console.log("File uploaded successfully, getting storage ID...");
            const storageId = await uploadResponse.text();

            console.log("Raw storage ID from upload response:", storageId);

            // Ensure we have a valid storage ID (not JSON)
            let finalStorageId = storageId;
            if (storageId.startsWith("{") && storageId.includes("storageId")) {
              try {
                const parsed = JSON.parse(storageId);
                finalStorageId = parsed.storageId;
                console.log("Parsed storage ID from JSON:", finalStorageId);
              } catch (e) {
                console.error("Failed to parse storage ID, using as-is:", e);
                // Continue with the original value if parsing fails
              }
            }

            // Get the Convex file ID
            console.log(
              "Saving storage ID to Convex database:",
              finalStorageId
            );
            const fileId = await convex.mutation(api.files.saveStorageId, {
              filename: sanitizedName,
              contentType: value.type || "application/pdf",
              storageId: finalStorageId,
            });

            console.log("File saved in Convex with ID:", fileId);
            uploadedFileIds.push(fileId);
          } catch (convexError) {
            console.error("Convex API error:", convexError);
            throw convexError;
          }
        } catch (uploadError) {
          console.error(
            `Error uploading file ${value.name} to Convex:`,
            uploadError
          );
          throw new Error(
            `Failed to upload file ${value.name} to Convex: ${
              uploadError instanceof Error
                ? uploadError.message
                : String(uploadError)
            }`
          );
        }
      }
    }

    // Log uploaded files to verify
    console.log("Files uploaded to Convex:", uploadedFileIds);

    // Return the uploaded file IDs in the response
    return NextResponse.json(
      {
        message: "success",
        uploadedFiles: uploadedFileIds,
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

    // Process Convex file IDs
    let convexFileIds: string[] = [];
    if (uploadedFilesParam) {
      convexFileIds = uploadedFilesParam
        .split(",")
        .filter((file) => file.trim() !== "");
    }

    console.log("Processing GET request with Convex file IDs:", convexFileIds);

    // Initialize Convex client for later use in cleanup
    const convex = new ConvexHttpClient(
      process.env.NEXT_PUBLIC_CONVEX_URL || ""
    );

    // Initialize fileData array to store file information
    const fileUrls: string[] = [];

    // Check if we have files to process
    if (convexFileIds.length === 0) {
      console.warn("No uploaded files found for processing");
    } else {
      try {
        // Retrieve download URLs for each file ID
        for (const fileId of convexFileIds) {
          // Get download URL directly from Convex
          const downloadUrl = await convex.query(api.files.getDownloadUrl, {
            id: fileId as Id<"files">,
          });

          if (!downloadUrl) {
            console.warn(`Failed to get download URL for file ID ${fileId}`);
            continue;
          }

          // Add URL to our list
          fileUrls.push(downloadUrl);
        }

        if (fileUrls.length === 0 && convexFileIds.length > 0) {
          return NextResponse.json(
            {
              message: "Failed to retrieve any file URLs from Convex",
            },
            { status: 400 }
          );
        }

        console.log(
          `Successfully retrieved ${fileUrls.length} file URLs from Convex`
        );
      } catch (convexError) {
        console.error("Error retrieving files from Convex:", convexError);
        return NextResponse.json(
          { message: "Failed to retrieve files from storage" },
          { status: 500 }
        );
      }
    }

    // Use the service to generate questions with streaming
    const result = await generateQuestions({
      questionHeader,
      questionDescription,
      apiKey,
      fileUrls,
      modelName,
    });

    // Function to delete files from Convex
    const cleanupConvexFiles = async () => {
      try {
        console.log("Cleaning up Convex files...");
        for (const fileId of convexFileIds) {
          try {
            console.log(`Attempting to delete Convex file with ID: ${fileId}`);
            await convex.mutation(api.files.deleteFile, {
              id: fileId as Id<"files">,
            });
            console.log(`Successfully deleted Convex file: ${fileId}`);
          } catch (deleteError) {
            console.error(`Error deleting Convex file ${fileId}:`, deleteError);
          }
        }
        console.log("Convex file cleanup completed");
      } catch (cleanupError) {
        console.error("Error during Convex file cleanup:", cleanupError);
      }
    };

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

          // Clean up Convex files after successful streaming
          await cleanupConvexFiles();

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

          // Clean up Convex files even if we had streaming errors
          await cleanupConvexFiles();

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
      // Clean up Convex files if we don't have a stream
      await cleanupConvexFiles();

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
