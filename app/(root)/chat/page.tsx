"use client";

import { InteractiveHoverButton } from "@/components/eldoraui/interactivebutton";
import { Inputp } from "@/components/ui/apikeyinput";
import { ColourfulText } from "@/components/ui/colourful-text";
import { FileUpload } from "@/components/ui/file-upload";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MainMenusGradientCard } from "@/components/eldoraui/animatedcard";
import { ArrowButton } from "@/components/eldoraui/arrowbutton";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import ReactMarkdown from "react-markdown";
import React, { useState, useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";

function Page() {
  const [files, setFiles] = useState<File[]>([]);
  const [questionHeader, setQuestionHeader] = useState("");
  const [questionDescription, setQuestionDescription] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelName, setModelName] = useState("qwen/qwq-32b:free");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [isFormSubmitted, setIsFormSubmitted] = useState(false);
  const [isAnimating, setIsAnimating] = useState(false);
  const [generatedContent, setGeneratedContent] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [uploadedFileNames, setUploadedFileNames] = useState<string[]>([]);
  const eventSourceRef = useRef<EventSource | null>(null);
  const [typingText, setTypingText] = useState("");
  const [isTypingComplete, setIsTypingComplete] = useState(false);
  const [displayedContentLength, setDisplayedContentLength] = useState(0);
  const typingSpeed = 5; // Characters per frame
  const typingInterval = useRef<NodeJS.Timeout | null>(null);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);
  const lastScrollPosition = useRef(0);
  const scrolling = useRef(false);

  const handleFileUpload = (files: File[]) => {
    setFiles(files);
  };

  // Cleanup function to close EventSource
  const cleanupEventSource = () => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  };

  // Effect to handle cleanup when component unmounts
  useEffect(() => {
    return () => {
      cleanupEventSource();
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate inputs
    if (!questionHeader.trim()) {
      setError("Question header is required");
      return;
    }

    if (!questionDescription.trim()) {
      setError("Question description is required");
      return;
    }

    if (!apiKey.trim()) {
      setError("API key is required");
      return;
    }

    if (files.length === 0) {
      setError("Please upload at least one file");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      // Create form data for file upload
      const formData = new FormData();
      files.forEach((file, index) => {
        formData.append(`file-${index}`, file);
      });

      formData.append("questionHeader", questionHeader);
      formData.append("questionDescription", questionDescription);
      formData.append("apiKey", apiKey);
      formData.append("modelName", modelName);

      console.log("Uploading files...");

      // Send data to your API endpoint
      const response = await fetch("/api/generate-questions", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.message || "Failed to generate questions");
      }

      const data = await response.json();
      console.log("Upload response:", data);

      // Store uploaded file names from response
      if (data.uploadedFiles && data.uploadedFiles.length > 0) {
        console.log("Files uploaded successfully:", data.uploadedFiles);

        // Set the state and then start fetching via a callback to ensure state is updated
        setUploadedFileNames(data.uploadedFiles);

        // Use a small timeout to ensure state is updated before proceeding
        setTimeout(() => {
          fetchGeneratedQuestions(data.uploadedFiles);
        }, 50);
      } else {
        console.warn("No files were uploaded in the response");
        setError("No files were processed. Please try again.");
        setIsLoading(false);
      }
    } catch (err: unknown) {
      setError(
        err instanceof Error
          ? err.message
          : "Something went wrong. Please try again."
      );
      setIsLoading(false);
    }
  };

  // Update fetchGeneratedQuestions to accept direct file list
  const fetchGeneratedQuestions = (files: string[] = []) => {
    // Clear previous content and set generating state
    setGeneratedContent("");
    setDisplayedContentLength(0);
    setTypingText("");
    setIsTypingComplete(false);
    setIsGenerating(true);
    setError("");

    // Clean up any existing EventSource
    cleanupEventSource();

    // Build URL with query parameters
    const params = new URLSearchParams();
    params.set("questionHeader", questionHeader);
    params.set("questionDescription", questionDescription);
    params.set("apiKey", apiKey);
    params.set("modelName", modelName);

    // Use the passed files parameter (from POST response) or fall back to state
    const filesToUse = files.length > 0 ? files : uploadedFileNames;

    // Add uploaded files to query if available
    if (filesToUse.length > 0) {
      console.log("Adding uploaded files to request:", filesToUse);
      params.set("uploadedFiles", filesToUse.join(","));
    } else {
      console.warn("No uploaded files to add to request");
    }

    const requestUrl = `/api/generate-questions?${params.toString()}`;
    console.log("Making SSE request to:", requestUrl);

    // Create EventSource for Server-Sent Events
    const eventSource = new EventSource(requestUrl);
    eventSourceRef.current = eventSource;

    // Handle incoming messages
    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        console.log("Received data:", data);

        // Only now transition to results view when first real data arrives
        if (!isFormSubmitted && data.content && data.content.trim() !== "") {
          setIsAnimating(true);

          // Use timeout to create smooth transition
          setTimeout(() => {
            setIsFormSubmitted(true);
            setIsAnimating(false);
            setIsLoading(false); // Stop loading state

            // Initial scroll to bottom after view transition
            setTimeout(() => scrollToBottom(), 100);
          }, 500);
        }

        if (data.type === "error") {
          setError(data.content);
          setIsGenerating(false);
          setIsLoading(false);
          cleanupEventSource();
          return;
        }

        // Add new content
        setGeneratedContent((prev) => {
          const newContent = prev + (prev ? "\n\n" : "") + data.content;

          // Queue scroll to bottom after content update
          requestAnimationFrame(() => {
            if (isGenerating) scrollToBottom();
          });

          return newContent;
        });
      } catch (e) {
        console.error("Failed to parse SSE data:", e);
        // If parsing fails, just add the raw content
        setGeneratedContent((prev) => prev + (prev ? "\n\n" : "") + event.data);
      }
    };

    // Handle connection open
    eventSource.onopen = () => {
      console.log("EventSource connection established");
    };

    // Handle errors
    eventSource.onerror = (error) => {
      console.error("EventSource error:", error);
      setError("Error receiving data from the server. Please try again.");
      setIsGenerating(false);
      cleanupEventSource();
    };

    // Handle completion event
    eventSource.addEventListener("complete", () => {
      console.log("Generation complete");
      setIsGenerating(false);
      cleanupEventSource();
    });
  };

  const handleGoBack = () => {
    cleanupEventSource();
    setIsAnimating(true);
    setTimeout(() => {
      setIsFormSubmitted(false);
      setIsAnimating(false);
      setGeneratedContent("");
      setIsGenerating(false);
    }, 500);
  };

  // Effect to animate typing when content is received
  useEffect(() => {
    if (!generatedContent || !isFormSubmitted || isGenerating) return;

    // Clear any existing interval
    if (typingInterval.current) {
      clearInterval(typingInterval.current);
    }

    setIsTypingComplete(false);

    // Start the typing animation
    typingInterval.current = setInterval(() => {
      setDisplayedContentLength((prev) => {
        if (prev + typingSpeed >= generatedContent.length) {
          clearInterval(typingInterval.current!);
          setIsTypingComplete(true);
          return generatedContent.length;
        }
        return prev + typingSpeed;
      });
    }, 10);

    return () => {
      if (typingInterval.current) {
        clearInterval(typingInterval.current);
      }
    };
  }, [generatedContent, isFormSubmitted, isGenerating]);

  // Update typing text when content length changes
  useEffect(() => {
    setTypingText(generatedContent.substring(0, displayedContentLength));
  }, [displayedContentLength, generatedContent]);

  // Function to handle scrolling to bottom with smooth animation
  const scrollToBottom = () => {
    if (contentRef.current && !scrolling.current) {
      scrolling.current = true;

      // Smooth scroll to bottom
      contentRef.current.scrollTo({
        top: contentRef.current.scrollHeight,
        behavior: "smooth",
      });

      // Reset scrolling flag after animation completes
      setTimeout(() => {
        scrolling.current = false;

        // Double check we're at bottom (handles very fast content updates)
        if (contentRef.current && isGenerating) {
          const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
          if (scrollHeight - scrollTop - clientHeight > 50) {
            contentRef.current.scrollTop = contentRef.current.scrollHeight;
          }
        }
      }, 300);
    }
  };

  // Enhanced effect to monitor content changes and auto-scroll during generation
  useEffect(() => {
    if (isGenerating && contentRef.current) {
      // Store last scroll position
      lastScrollPosition.current = contentRef.current.scrollHeight;

      // Use requestAnimationFrame for smoother updates
      requestAnimationFrame(() => {
        scrollToBottom();
      });
    }
  }, [generatedContent, isGenerating]);

  // Handle auto-scrolling when typing animation advances
  useEffect(() => {
    if (isGenerating && contentRef.current && typingText) {
      // Only auto-scroll if user hasn't manually scrolled up
      const { scrollTop, scrollHeight, clientHeight } = contentRef.current;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;

      if (isNearBottom) {
        requestAnimationFrame(() => {
          scrollToBottom();
        });
      }
    }
  }, [typingText, isGenerating]);

  // Check if scroll button should be shown
  const handleContentScroll = () => {
    if (!contentRef.current) return;

    const { scrollHeight, clientHeight } = contentRef.current;

    setShowScrollButton(scrollHeight > clientHeight + 20);
  };

  // Add scroll event listener
  useEffect(() => {
    const currentRef = contentRef.current;
    if (currentRef) {
      currentRef.addEventListener("scroll", handleContentScroll);
      // Initial check
      handleContentScroll();
    }

    return () => {
      if (currentRef) {
        currentRef.removeEventListener("scroll", handleContentScroll);
      }
    };
  }, [isFormSubmitted]);

  // Add an effect to check for scroll button visibility when content changes
  useEffect(() => {
    if (contentRef.current) {
      // Force recalculation of scroll button visibility when content changes
      setTimeout(handleContentScroll, 100);
    }
  }, [generatedContent, typingText]);

  // Render the content with animation
  const renderAnimatedContent = () => {
    if (isGenerating) {
      return (
        <div
          ref={contentRef}
          className="relative overflow-y-scroll overflow-x-hidden overflow-scrollbar-hidden max-h-[75vh] scroll-smooth"
        >
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mb-4"
          >
            Generating content based on your uploaded files...
          </motion.p>
          {generatedContent && (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              className="mt-4 border-t border-neutral-700 pt-4"
            >
              <p className="text-sm text-green-400 mb-2">
                Content generated so far:
              </p>
              <div className="prose prose-invert max-w-none">
                <ReactMarkdown>{typingText}</ReactMarkdown>
                <motion.span
                  className="inline-block w-1 h-5 bg-blue-400 ml-0.5"
                  animate={{ opacity: [1, 0, 1] }}
                  transition={{ repeat: Infinity, duration: 1 }}
                />
              </div>
            </motion.div>
          )}

          {/* Floating scroll button */}
          {showScrollButton && (
            <motion.button
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              onClick={scrollToBottom}
              className="fixed right-4 bottom-4 rounded-full bg-blue-600 hover:bg-blue-500 p-3 shadow-lg transition-colors z-50"
              aria-label="Scroll to bottom"
            >
              <ChevronDown className="h-5 w-5 text-white" />
            </motion.button>
          )}
        </div>
      );
    }

    if (!generatedContent) {
      return (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          className="text-yellow-400"
        >
          No content generated yet. There might have been an error.
          {error && <p className="text-red-400 mt-2">{error}</p>}
        </motion.div>
      );
    }

    return (
      <motion.div
        ref={contentRef}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="w-full overflow-y-scroll overflow-x-hidden overflow-scrollbar-hidden max-h-[75vh] relative scroll-smooth"
        onScroll={handleContentScroll}
      >
        <div className="prose prose-invert max-w-none">
          <ReactMarkdown>{typingText}</ReactMarkdown>
          {!isTypingComplete && (
            <motion.span
              className="inline-block w-1 h-5 bg-blue-400 ml-0.5"
              animate={{ opacity: [1, 0, 1] }}
              transition={{ repeat: Infinity, duration: 1 }}
            />
          )}
        </div>

        {/* Floating scroll button */}
        {showScrollButton && (
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            onClick={scrollToBottom}
            className="fixed right-4 bottom-4 rounded-full bg-neutral-600 hover:bg-neutral-400 p-3 shadow-lg transition-colors z-50"
            aria-label="Scroll to bottom"
          >
            <ChevronDown className="h-5 w-5 text-white" />
          </motion.button>
        )}
      </motion.div>
    );
  };

  return (
    <div className="bg-gradient-to-b from-neutral-950 to-neutral-800 min-h-screen transition-all duration-500">
      <div className="mx-4 py-6 text-4xl">
        <Link href="/">
          <ColourfulText text="QuestGen" />
        </Link>
      </div>

      <AnimatePresence mode="wait">
        {!isFormSubmitted ? (
          <motion.div
            key="form"
            initial={{ opacity: 0, y: 20 }}
            animate={{
              opacity: isAnimating ? 0 : 1,
              y: isAnimating ? -20 : 0,
              scale: isAnimating ? 0.95 : 1,
            }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.4 }}
            className="w-full"
          >
            <form
              onSubmit={handleSubmit}
              className="flex flex-col  items-center justify-center gap-2 pt-10 pb-10 px-4"
            >
              {error && (
                <div className="w-full max-w-7xl p-4 bg-red-500/20 border border-red-500 rounded-md text-red-100">
                  {error}
                </div>
              )}

              <div className="w-full  max-w-7xl mx-auto border border-dashed border-neutral-700">
                <FileUpload onChange={handleFileUpload} />
                {files.length > 0 && (
                  <div className="p-4 text-sm text-neutral-300">
                    {files.length} file(s) selected
                  </div>
                )}
              </div>

              <div className="w-full max-w-7xl">
                <Label className="text-2xl " htmlFor="questionHeader">
                  Question Header Section
                </Label>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-1 text-xs text-neutral-300 my-1">
                  <div>- Institution Name</div>
                  <div>- Exam Type</div>
                  <div>- Course Name</div>
                  <div>- Course Code</div>
                  <div>- Semester</div>
                  <div>- Duration/Marks</div>
                </div>
                <Input
                  id="questionHeader"
                  value={questionHeader}
                  onChange={(e) => setQuestionHeader(e.target.value)}
                  placeholder="Enter the Question Header"
                  className="h-16 mt-2 placeholder:w-[19%] placeholder:text-lg"
                />
              </div>

              <div className="w-full max-w-7xl">
                <Label className="text-2xl" htmlFor="questionDescription">
                  Question Description
                </Label>
                <Input
                  id="questionDescription"
                  value={questionDescription}
                  onChange={(e) => setQuestionDescription(e.target.value)}
                  placeholder="Enter Brief Description of the Question"
                  className="h-20 mt-2 placeholder:text-lg"
                />
              </div>

              <div className="w-full flex flex-col md:flex-row justify-between items-center max-w-7xl gap-4">
                <div className="w-full md:w-1/2 flex flex-col md:flex-row gap-4">
                  <Inputp
                    id="apiKey"
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="Enter API Key"
                    className="w-full"
                  />

                  {/* Model Name Input */}
                  <Inputp
                    id="modelName"
                    type="text"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    placeholder="Model Name"
                    className="w-full"
                  />
                </div>

                {!isLoading ? (
                  <button type="submit" className="w-full md:w-auto">
                    <InteractiveHoverButton
                      text="Generate Question Paper"
                      className="my-8 w-full"
                    />
                  </button>
                ) : (
                  <motion.div
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    className="w-full md:w-auto p-4 text-center"
                  >
                    <div className="flex items-center justify-center space-x-2">
                      <div
                        className="w-3 h-3 rounded-full bg-blue-500 animate-bounce"
                        style={{ animationDelay: "0ms" }}
                      ></div>
                      <div
                        className="w-3 h-3 rounded-full bg-blue-500 animate-bounce"
                        style={{ animationDelay: "300ms" }}
                      ></div>
                      <div
                        className="w-3 h-3 rounded-full bg-blue-500 animate-bounce"
                        style={{ animationDelay: "600ms" }}
                      ></div>
                    </div>
                    <p className="text-neutral-300 mt-3">
                      Generating, please wait...
                    </p>
                  </motion.div>
                )}
              </div>
            </form>
          </motion.div>
        ) : (
          <motion.div
            key="results"
            initial={{ opacity: 0, y: 20 }}
            animate={{
              opacity: isAnimating ? 0 : 1,
              y: isAnimating ? 20 : 0,
            }}
            exit={{ opacity: 0, y: 20 }}
            transition={{ duration: 0.4 }}
            className="w-full"
          >
            <div className="flex justify-center gap-10 min-h-[78vh] h-auto">
              <div className="w-fit mt-10 pl-5">
                <div onClick={handleGoBack} className="cursor-pointer">
                  <ArrowButton />
                </div>
              </div>
              <div className="grid h-full w-[80%]">
                <MainMenusGradientCard
                  className="p-4 text-xl backdrop-blur-sm"
                  description={`Based on:\n${questionHeader}\n\n${questionDescription}`}
                  title={
                    isGenerating
                      ? "Generating Your Question Paper..."
                      : "Generated Question Paper"
                  }
                >
                  {renderAnimatedContent()}
                </MainMenusGradientCard>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default Page;
