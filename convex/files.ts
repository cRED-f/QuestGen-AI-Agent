import { mutation, query } from "./_generated/server";
import { ConvexError, v } from "convex/values";

// Generate a URL for uploading a file
export const generateUploadUrl = mutation({
  // Define the expected arguments
  args: {
    filename: v.string(),
    contentType: v.string(),
  },
  // Generate and return a URL for file upload
  handler: async (ctx) => {
    // Generate a URL that can be used to upload a file
    return await ctx.storage.generateUploadUrl();
  },
});

// Save the storage ID after a successful upload
export const saveStorageId = mutation({
  // Define the expected arguments
  args: {
    storageId: v.string(),
    filename: v.string(),
    contentType: v.string(),
  },
  // Save the storage ID in the database and return the document ID
  handler: async (ctx, args) => {
    // The storageId is already the correct format from the upload response
    // Just use it directly without parsing

    // Insert a new document in the "files" table
    const fileId = await ctx.db.insert("files", {
      storageId: args.storageId,
      filename: args.filename,
      contentType: args.contentType || "application/pdf",
      uploadedAt: Date.now(),
    });

    return fileId;
  },
});

// Get a file by ID
export const getFileById = query({
  // Define the expected arguments
  args: {
    id: v.id("files"),
  },
  // Retrieve the file document from the database
  handler: async (ctx, args) => {
    const file = await ctx.db.get(args.id);
    if (!file) {
      throw new ConvexError(`File with ID ${args.id} not found`);
    }
    return file;
  },
});

// Get a URL for downloading a file
export const getDownloadUrl = query({
  // Define the expected arguments
  args: {
    id: v.id("files"),
  },
  // Generate and return a URL for downloading the file
  handler: async (ctx, args) => {
    try {
      // Get the file document from the database
      const file = await ctx.db.get(args.id);
      if (!file) {
        throw new ConvexError(`File with ID ${args.id} not found`);
      }

      // Extract the actual storage ID string from the stored value
      // The error suggests it might be stored as a string containing JSON
      let storageId = file.storageId;

      // If storageId looks like JSON, parse it
      if (storageId.startsWith("{") && storageId.includes("storageId")) {
        try {
          const parsed = JSON.parse(storageId);
          storageId = parsed.storageId;
        } catch (e) {
          console.error("Failed to parse storage ID:", e);
          // Continue with the original value if parsing fails
        }
      }

      // Generate a URL for downloading the file using the corrected storageId
      return await ctx.storage.getUrl(storageId);
    } catch (error: unknown) {
      console.error("Error in getDownloadUrl:", error);
      const errorMessage =
        error instanceof Error ? error.message : String(error);
      throw new ConvexError(`Failed to get download URL: ${errorMessage}`);
    }
  },
});

// List all files
export const listFiles = query({
  handler: async (ctx) => {
    return await ctx.db.query("files").order("desc").collect();
  },
});

// Delete a file by ID
export const deleteFile = mutation({
  // Define the expected arguments
  args: {
    id: v.id("files"),
  },
  // Delete the file and its document
  handler: async (ctx, args) => {
    // Get the file document from the database
    const file = await ctx.db.get(args.id);
    if (!file) {
      throw new ConvexError(`File with ID ${args.id} not found`);
    }

    // Delete the file from storage
    await ctx.storage.delete(file.storageId);

    // Delete the file document from the database
    await ctx.db.delete(args.id);

    return { success: true };
  },
});
