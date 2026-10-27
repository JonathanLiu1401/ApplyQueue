import LLMSettingsForm from "../components/forms/LLMSettingsForm";
import { PageHeader } from "../components/ui";
import AgentBackendsSection from "../components/settings/AgentBackendsSection";
import { useTranslation } from "react-i18next";

export default function LLMSettingsPage() {
  const { t } = useTranslation("llm");
  return (
    <div className="max-w-2xl">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <LLMSettingsForm />
      <div className="mt-5">
        <AgentBackendsSection />
      </div>
    </div>
  );
}
