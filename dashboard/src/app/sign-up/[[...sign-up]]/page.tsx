import { SignUp } from "@clerk/nextjs";

export default function SignUpPage() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-4 py-16">
      <div className="flex flex-col items-center gap-2">
        <span className="text-lg font-semibold tracking-tight text-neutral-900">
          Rufo
        </span>
        <p className="text-sm text-neutral-500">Create your organization&apos;s account</p>
      </div>
      <SignUp
        appearance={{
          elements: {
            rootBox: "w-full max-w-sm",
            card: "shadow-sm border border-neutral-200 rounded-xl",
            headerTitle: "hidden",
            headerSubtitle: "hidden",
            socialButtonsBlockButton:
              "border border-neutral-200 hover:bg-neutral-50",
            formButtonPrimary:
              "bg-neutral-900 hover:bg-neutral-800 text-sm normal-case",
            footerActionLink: "text-neutral-900 hover:text-neutral-700",
          },
          variables: {
            colorPrimary: "#171717",
            borderRadius: "0.625rem",
          },
        }}
      />
    </div>
  );
}
