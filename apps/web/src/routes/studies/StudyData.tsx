import { StudyPage } from "./Study";
export function StudyData({ params }: { params: { studyId: string } }) {
  return <StudyPage studyId={params.studyId} page="data" />;
}
