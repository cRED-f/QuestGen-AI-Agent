import { defineSchema, defineTable } from "convex/server";
import { v } from "convex/values";

export default defineSchema({
  // Files table to store metadata about uploaded files
  files: defineTable({
    storageId: v.string(),
    filename: v.string(),
    contentType: v.string(),
    uploadedAt: v.number(), // Unix timestamp
  }),

  // You can add more tables here as needed for your application
  // For example, if you want to track question generation sessions:

  questionSessions: defineTable({
    questionHeader: v.string(),
    questionDescription: v.string(),
    fileIds: v.array(v.id("files")), // References to uploaded files
    status: v.string(), // e.g., "pending", "completed", "failed"
    result: v.optional(v.string()), // The generated questions
    createdAt: v.number(), // Unix timestamp
    completedAt: v.optional(v.number()), // Unix timestamp
  }).index("by_status", ["status"]),
});
