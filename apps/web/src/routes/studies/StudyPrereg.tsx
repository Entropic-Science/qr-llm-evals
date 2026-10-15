import { StudyPage } from "./Study";
export function StudyPrereg({ params }: { params: { studyId: string } }) {
  return <StudyPage studyId={params.studyId} page="prereg" />;
}
